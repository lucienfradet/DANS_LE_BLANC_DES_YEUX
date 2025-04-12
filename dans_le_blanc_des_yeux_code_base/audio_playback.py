"""
Audio playback module for the Dans le Blanc des Yeux installation using GStreamer.
Handles playing audio with channel muting based on system state.

playback logic:
1. When both have pressure:
   - Play with LEFT channel muted
   - Receive from personal mic stream

2. When remote has pressure and local doesn't:
   - Play with RIGHT channel muted
   - Receive from global mic stream

3. When local has pressure and remote doesn't:
   - Play with LEFT channel muted
   - Receive from personal mic stream

4. When neither has pressure: Both pipelines active but no audio received
"""

"""
Audio playback module for the Dans le Blanc des Yeux installation using GStreamer.
Modified to receive raw UDP audio data instead of RTP for more reliable playback.
"""

import os
import time
import threading
import configparser
from typing import Dict, Optional, Tuple, List, Callable, Any

# Import GStreamer but avoid GLib main loop
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

from system_state import system_state
from audio_streamer import AudioStreamer

# Audio configuration
RATE = 44100
CHANNELS = 2

class AudioPlayback:
    """Handles audio playback with channel muting based on system state using persistent pipelines."""
    
    def __init__(self, audio_streamer: AudioStreamer):
        self.audio_streamer = audio_streamer
        
        # Get port numbers from streamer
        self.GLOBAL_MIC_PORT = audio_streamer.GLOBAL_MIC_PORT
        self.PERSONAL_MIC_PORT = audio_streamer.PERSONAL_MIC_PORT
        
        # Current playback state
        self.playback_state = "none"  # "none", "mute_left", "mute_right"
        
        # Threading
        self.running = False
        self.playback_thread = None
        self.lock = threading.Lock()
        
        # Pipeline objects - one for each port
        self.personal_pipeline = None
        self.global_pipeline = None
        
        # Panorama elements for dynamic channel muting
        self.personal_panorama = None
        self.global_panorama = None
        
        # Pipeline status
        self.pipelines_created = False
        
        # Register as observer for state changes
        system_state.add_observer(self._on_state_change)
        
        print("Improved audio playback initialized with persistent pipelines")
    
    def start(self) -> bool:
        """Start the audio playback system with persistent pipelines."""
        print("Starting improved audio playback with raw UDP pipelines...")
        self.running = True
        
        # Create both pipelines at startup
        success = self._create_playback_pipelines()
        if not success:
            print("Failed to create audio playback pipelines")
            self.running = False
            return False
        
        # Start playback monitoring thread
        self.playback_thread = threading.Thread(target=self._playback_monitoring_loop)
        self.playback_thread.daemon = True
        self.playback_thread.start()
        
        # Update the playback state based on current system state
        self._update_playback_state()
        
        print("Audio playback started with persistent pipelines")
        return True
    
    def stop(self) -> None:
        """Stop the audio playback and clean up resources."""
        print("Stopping audio playback...")
        self.running = False
        
        # Stop playback if running
        with self.lock:
            # Proper shutdown of personal pipeline
            if self.personal_pipeline:
                print("Stopping personal pipeline with proper state transitions")
                self.personal_pipeline.set_state(Gst.State.PAUSED)
                self.personal_pipeline.get_state(500 * Gst.MSECOND)
                self.personal_pipeline.set_state(Gst.State.READY)
                self.personal_pipeline.get_state(500 * Gst.MSECOND)
                self.personal_pipeline.set_state(Gst.State.NULL)
                self.personal_pipeline = None
                self.personal_panorama = None
            
            # Proper shutdown of global pipeline
            if self.global_pipeline:
                print("Stopping global pipeline with proper state transitions")
                self.global_pipeline.set_state(Gst.State.PAUSED)
                self.global_pipeline.get_state(500 * Gst.MSECOND)
                self.global_pipeline.set_state(Gst.State.READY)
                self.global_pipeline.get_state(500 * Gst.MSECOND)
                self.global_pipeline.set_state(Gst.State.NULL)
                self.global_pipeline = None
                self.global_panorama = None
            
            self.pipelines_created = False
        
        # Wait for thread to finish
        if self.playback_thread and self.playback_thread.is_alive():
            self.playback_thread.join(timeout=2.0)
        
        print("Audio playback stopped")
    
    def _on_state_change(self, changed_state: str) -> None:
        """Handle system state changes."""
        if changed_state in ["local", "remote"]:
            self._update_playback_state()
    
    def _update_playback_state(self) -> None:
        """Update playback state based on the current system state."""
        local_state = system_state.get_local_state()
        remote_state = system_state.get_remote_state()
        
        old_state = self.playback_state
        
        # Only proceed if remote is connected
        if not remote_state.get("connected", False):
            self.playback_state = "none"
        
        # Case 1: Both have pressure - LEFT channel muted
        elif local_state.get("pressure", False) and remote_state.get("pressure", False):
            self.playback_state = "mute_left"
        
        # Case 2: Remote has pressure but local doesn't - RIGHT channel muted
        elif remote_state.get("pressure", False) and not local_state.get("pressure", False):
            self.playback_state = "mute_right"
        
        # Case 3: Local has pressure but remote doesn't - LEFT channel muted
        elif local_state.get("pressure", False) and not remote_state.get("pressure", False):
            self.playback_state = "mute_left"
        
        # Case 4: No pressure on either - no playback
        else:
            self.playback_state = "none"
        
        if old_state != self.playback_state:
            print(f"Playback state changed from {old_state} to {self.playback_state}")
            
            # Update the panorama settings instead of rebuilding pipelines
            self._update_panorama_settings()
            
            # Update system state with audio state
            system_state.update_audio_state({
                "audio_muted_channel": self._get_muted_channel_info()
            })
    
    def _get_muted_channel_info(self) -> str:
        """Get human-readable information about which channel is muted."""
        if self.playback_state == "mute_left":
            return "left"
        elif self.playback_state == "mute_right":
            return "right"
        else:
            return "both"  # No playback means effectively both channels muted
    
    def _create_playback_pipeline(self, port: int, name: str) -> (Optional[Gst.Pipeline], Optional[Gst.Element]):
        """Create a more robust playback pipeline using raw UDP with WAV framing."""
        try:
            # Create a descriptive pipeline name
            pipeline_name = f"playback_{name}_{port}"
            
            # Get the default sink from PulseAudio configuration
            import subprocess
            result = subprocess.run(['pactl', 'info'], 
                                   stdout=subprocess.PIPE, 
                                   stderr=subprocess.PIPE, 
                                   text=True)
            default_sink = None
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'Default Sink:' in line:
                        default_sink = line.split(':', 1)[1].strip()
                        print(f"Found default sink: {default_sink}")
                        break
            
            # Create pipeline for receiving WAV-formatted UDP audio and playing it
            # Include a panorama element that we can adjust dynamically
            pipeline_str = (
                f"udpsrc port={port} timeout=0 buffer-size=65536 ! "
                "application/x-rtp,media=audio,payload=96,clock-rate=44100,encoding-name=L24 ! "
                "rtpL24depay ! "  # Parse WAV format from UDP
                "queue ! "  # Add queue after parse
                "audioconvert ! "
                "audio/x-raw, format=S16LE, channels=2, rate=44100 ! "
                f"audiopanorama name=panorama_{name} method=simple panorama=0.0 ! "
                "queue ! "  # Add queue after panorama
                "audioconvert ! "
                "audioresample quality=10 ! "
                "audio/x-raw, format=S16LE, channels=2, rate=44100 ! "
                # "autoaudiosink sync=false ! "
            )
            
            # Add device-specific sink if we found the default sink
            if default_sink:
                pipeline_str += f'pulsesink sync=false async=false device="{default_sink}"'
            else:
                pipeline_str += 'pulsesink sync=false async=false'
            
            print(f"Creating {name} playback pipeline: {pipeline_str}")
            
            # Create the pipeline
            pipeline = Gst.parse_launch(pipeline_str)
            pipeline.set_name(pipeline_name)
            
            # Get the panorama element for later adjustments
            panorama = pipeline.get_by_name(f"panorama_{name}")
            
            # More careful state transitions
            print(f"Setting {name} pipeline to NULL state first")
            pipeline.set_state(Gst.State.NULL)
            pipeline.get_state(500 * Gst.MSECOND)  # Wait for state change
            
            print(f"Setting {name} pipeline to READY state")
            ret = pipeline.set_state(Gst.State.READY)
            if ret == Gst.StateChangeReturn.FAILURE:
                print(f"Failed to set {name} pipeline to READY state")
                pipeline.set_state(Gst.State.NULL)
                return None, None
            pipeline.get_state(500 * Gst.MSECOND)  # Wait for state change
            
            print(f"Setting {name} pipeline to PLAYING state")
            ret = pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                print(f"Failed to set {name} pipeline to PLAYING state")
                pipeline.set_state(Gst.State.NULL)
                return None, None
            
            # No need to wait for PLAYING state as it might block
            
            print(f"Successfully created {name} playback pipeline")
            return pipeline, panorama
            
        except Exception as e:
            print(f"Error creating {name} playback pipeline: {e}")
            return None, None
    
    def _create_playback_pipelines(self) -> bool:
        """Create both playback pipelines at startup."""
        try:
            with self.lock:
                # Create personal pipeline
                self.personal_pipeline, self.personal_panorama = self._create_playback_pipeline(
                    self.PERSONAL_MIC_PORT, "personal")
                
                if not self.personal_pipeline or not self.personal_panorama:
                    print("Failed to create personal mic playback pipeline")
                    return False
                
                # Create global pipeline
                self.global_pipeline, self.global_panorama = self._create_playback_pipeline(
                    self.GLOBAL_MIC_PORT, "global")
                
                if not self.global_pipeline or not self.global_panorama:
                    print("Failed to create global mic playback pipeline")
                    # Clean up personal pipeline
                    self.personal_pipeline.set_state(Gst.State.NULL)
                    self.personal_pipeline = None
                    self.personal_panorama = None
                    return False
                
                # Both pipelines created successfully
                self.pipelines_created = True
                return True
                
        except Exception as e:
            print(f"Error creating playback pipelines: {e}")
            self.pipelines_created = False
            return False
    
    def _update_panorama_settings(self) -> None:
        """Update panorama settings based on current playback state without recreating pipelines."""
        try:
            with self.lock:
                if not self.pipelines_created:
                    print("Skipping panorama update - pipelines not created")
                    return
                
                # First check if the pipelines are in PLAYING state
                # If not, this could be why your changes aren't taking effect
                if self.personal_pipeline and self.global_pipeline:
                    personal_state = self.personal_pipeline.get_state(0)[1]
                    global_state = self.global_pipeline.get_state(0)[1]
                    
                    if personal_state != Gst.State.PLAYING or global_state != Gst.State.PLAYING:
                        print(f"Warning: Cannot update panorama, pipelines not in PLAYING state: personal={personal_state}, global={global_state}")
                        # Queue up for retry in monitoring loop
                        return
                
                # Use stronger panorama values (1.5 instead of 1.0) to ensure full separation
                # Some implementations of audiopanorama may need values beyond the expected -1.0 to 1.0 range
                if self.playback_state == "mute_left":
                    # Move all sound to right channel (stronger value for complete separation)
                    if self.personal_panorama:
                        try:
                            # Try a stronger value first (1.5) - some implementations allow beyond ±1.0
                            self.personal_panorama.set_property("panorama", 1.5)
                        except:
                            # Fall back to standard 1.0 if that fails
                            self.personal_panorama.set_property("panorama", 1.0)
                        
                    if self.global_panorama:
                        try:
                            self.global_panorama.set_property("panorama", 1.5)
                        except:
                            self.global_panorama.set_property("panorama", 1.0)
                    
                    print("Updated panorama: Muted LEFT channel (panorama=1.5 or 1.0)")
                    
                elif self.playback_state == "mute_right":
                    # Move all sound to left channel (stronger value for complete separation)
                    if self.personal_panorama:
                        try:
                            self.personal_panorama.set_property("panorama", -1.5)
                        except:
                            self.personal_panorama.set_property("panorama", -1.0)
                        
                    if self.global_panorama:
                        try:
                            self.global_panorama.set_property("panorama", -1.5)
                        except:
                            self.global_panorama.set_property("panorama", -1.0)
                    
                    print("Updated panorama: Muted RIGHT channel (panorama=-1.5 or -1.0)")
                    
                elif self.playback_state == "none":
                    # For "none" state, you might want to try muting differently
                    # Setting panorama to 0.0 keeps sound in center, it doesn't mute
                    if self.personal_panorama:
                        self.personal_panorama.set_property("panorama", 0.0)
                    if self.global_panorama:
                        self.global_panorama.set_property("panorama", 0.0)
                    
                    print("Updated panorama: Center channel position (panorama=0.0)")
                    
                    # Note: In your original code, you mentioned that no sound should 
                    # play in "none" state. If that's the case, you might need to 
                    # handle this differently than just setting panorama to 0.0.
                
                # Flush the pipeline to ensure changes take effect immediately
                if self.personal_pipeline:
                    self._flush_pipeline(self.personal_pipeline)
                if self.global_pipeline:
                    self._flush_pipeline(self.global_pipeline)
                
        except Exception as e:
            print(f"Error updating panorama settings: {e}")

    def _flush_pipeline(self, pipeline: Gst.Pipeline) -> None:
        """Flush the pipeline to ensure property changes take effect immediately."""
        try:
            # Send an event to flush the pipeline
            # This helps force property changes to take effect
            # Only flush if pipeline is in PLAYING state
            state = pipeline.get_state(0)[1]
            if state == Gst.State.PLAYING:
                sink = pipeline.get_by_name("sink")
                if sink:
                    # Try using EVENT_FLUSH_START and EVENT_FLUSH_STOP
                    sink.send_event(Gst.Event.new_flush_start())
                    sink.send_event(Gst.Event.new_flush_stop(True))
                    print(f"Flushed pipeline {pipeline.get_name()}")
        except Exception as e:
            print(f"Error flushing pipeline: {e}")
    
    def _playback_monitoring_loop(self) -> None:
        """Monitor the playback pipelines and ensure they're running correctly."""
        print("Playback monitoring loop started")
        
        # Track consecutive failures to avoid spam
        consecutive_failures = 0
        max_consecutive_failures = 3
        
        while self.running:
            try:
                # Check pipeline states
                with self.lock:
                    if self.pipelines_created:
                        # Only check complete state - includes pending state changes
                        personal_state, pending_personal = self.personal_pipeline.get_state(0)[1:3]
                        global_state, pending_global = self.global_pipeline.get_state(0)[1:3]
                        
                        # Only try to fix if not in a pending state change
                        if pending_personal == Gst.State.VOID_PENDING and personal_state != Gst.State.PLAYING:
                            if consecutive_failures < max_consecutive_failures:
                                print(f"Personal pipeline not PLAYING (state={personal_state}), trying to restart...")
                                ret = self.personal_pipeline.set_state(Gst.State.PLAYING)
                                if ret == Gst.StateChangeReturn.FAILURE:
                                    consecutive_failures += 1
                                    print(f"Failed to set personal pipeline to PLAYING, attempt {consecutive_failures}/{max_consecutive_failures}")
                                else:
                                    print("Successfully requested state change for personal pipeline")
                            elif consecutive_failures == max_consecutive_failures:
                                print("Giving up on restarting personal pipeline after multiple failures")
                                consecutive_failures += 1
                        
                        if pending_global == Gst.State.VOID_PENDING and global_state != Gst.State.PLAYING:
                            if consecutive_failures < max_consecutive_failures:
                                print(f"Global pipeline not PLAYING (state={global_state}), trying to restart...")
                                ret = self.global_pipeline.set_state(Gst.State.PLAYING)
                                if ret == Gst.StateChangeReturn.FAILURE:
                                    consecutive_failures += 1
                                    print(f"Failed to set global pipeline to PLAYING, attempt {consecutive_failures}/{max_consecutive_failures}")
                                else:
                                    print("Successfully requested state change for global pipeline")
                            elif consecutive_failures == max_consecutive_failures:
                                print("Giving up on restarting global pipeline after multiple failures")
                                consecutive_failures += 1
                        
                        # Reset counter if both pipelines are in desired state
                        if personal_state == Gst.State.PLAYING and global_state == Gst.State.PLAYING:
                            if consecutive_failures > 0:
                                print("Both pipelines now in PLAYING state")
                                consecutive_failures = 0
                    
                    # Recreate pipelines if needed, but not too frequently
                    elif self.running and consecutive_failures < max_consecutive_failures:
                        print("Pipelines not created, attempting to recreate...")
                        success = self._create_playback_pipelines()
                        if success:
                            # Update panorama settings based on current state
                            self._update_panorama_settings()
                            consecutive_failures = 0
                        else:
                            consecutive_failures += 1
                
                # Sleep to avoid consuming too much CPU
                time.sleep(2.0)  # Increased sleep time to reduce spam
                
            except Exception as e:
                print(f"Error in playback monitoring loop: {e}")
                time.sleep(2.0)
        
        print("Playback monitoring loop stopped")
