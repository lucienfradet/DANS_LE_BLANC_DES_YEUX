"""
Audio playback module for the Dans le Blanc des Yeux installation using GStreamer.
Handles playing audio with channel muting based on system state.

playback logic:
1. When both have pressure:
   - Play with LEFT channel muted
   - Send personal mic (TX) to remote

2. When remote has pressure and local doesn't:
   - Play with RIGHT channel muted
   - Send global mic (USB) to remote

3. When local has pressure and remote doesn't:
   - Play with LEFT channel muted
   - Send personal mic (TX) to remote

4. When neither has pressure: No playback
"""

"""
Minimal audio playback module for the Dans le Blanc des Yeux installation.
Uses direct GStreamer pipeline with NO GLib main loop to avoid X11/OpenCV conflicts.
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
    """Handles audio playback with channel muting based on system state."""
    
    def __init__(self, audio_streamer: AudioStreamer):
        self.audio_streamer = audio_streamer
        
        # Current playback state
        self.playback_state = "none"  # "none", "mute_left", "mute_right"
        
        # Threading
        self.running = False
        self.playback_thread = None
        self.lock = threading.Lock()
        
        # The actual playing pipeline
        self.pipeline = None
        self.app_source = None  # Add this for appsrc
        
        # Register for audio callbacks (needed for compatibility)
        self.audio_streamer.register_personal_mic_callback(self._on_received_audio)
        self.audio_streamer.register_global_mic_callback(self._on_received_audio)
        
        # Register as observer for state changes
        system_state.add_observer(self._on_state_change)
        
        print("Audio playback initialized")
    
    def start(self) -> bool:
        """Start the audio playback system."""
        print("Starting minimal audio playback...")
        self.running = True
        
        # Start playback thread
        self.playback_thread = threading.Thread(target=self._playback_loop)
        self.playback_thread.daemon = True
        self.playback_thread.start()
        
        # Update the playback state based on current system state
        self._update_playback_state()
        
        print("Audio playback started")
        return True
    
    def stop(self) -> None:
        """Stop the audio playback and clean up with improved pipeline teardown."""
        print("Stopping audio playback...")
        self.running = False
        
        # Stop playback if running
        with self.lock:
            if self.pipeline:
                print("Stopping pipeline with proper state transitions")
                # Proper state transitions
                self.pipeline.set_state(Gst.State.PAUSED)
                self.pipeline.get_state(500 * Gst.MSECOND)  # 500ms timeout
                self.pipeline.set_state(Gst.State.READY)
                self.pipeline.get_state(500 * Gst.MSECOND)  # 500ms timeout
                self.pipeline.set_state(Gst.State.NULL)
                self.pipeline = None
                self.app_source = None
        
        # Wait for thread to finish
        if self.playback_thread and self.playback_thread.is_alive():
            self.playback_thread.join(timeout=2.0)  # Longer timeout
        
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
    
    def _on_received_audio(self, data: bytes) -> None:
        """Handle received audio data (callback from audio_streamer)."""
        # Only process if we're playing and pipeline exists
        if self.playback_state != "none" and self.pipeline and self.app_source:
            # The data is already clean (header removed by audio_streamer._receiver_loop)
            try:
                # Create a GStreamer buffer
                buffer = Gst.Buffer.new_wrapped(data)
                # Push to pipeline
                self.app_source.emit("push-buffer", buffer)
            except Exception as e:
                print(f"Error pushing audio buffer: {e}")
    
    def _create_playback_pipeline(self) -> Optional[Gst.Pipeline]:
        """Create a playback pipeline using appsrc for clean audio data."""
        try:
            # Create a unique pipeline name based on current time
            pipeline_name = f"playback_{int(time.time())}"
            
            if self.playback_state == "none":
                return None
            
            # Create pipeline with appsrc
            pipeline_str = (
                "appsrc name=audio_source format=time is-live=true do-timestamp=true ! "
                "audio/x-raw, format=S16LE, channels=2, rate=44100, layout=interleaved ! "
                "queue max-size-bytes=65536 ! "
                "audioconvert ! audioresample ! "
            )
            
            # Add channel muting based on state
            if self.playback_state == "mute_left":
                pipeline_str += (
                    "audioconvert ! "
                    "audiopanorama method=simple panorama=1.0 ! "  # Move all sound to right channel
                )
            elif self.playback_state == "mute_right":
                pipeline_str += (
                    "audioconvert ! "
                    "audiopanorama method=simple panorama=-1.0 ! "  # Move all sound to left channel
                )
            
            # Add sink
            pipeline_str += "pulsesink sync=false"
            
            print(f"Creating playback pipeline: {pipeline_str}")
            
            # Create the pipeline
            pipeline = Gst.parse_launch(pipeline_str)
            pipeline.set_name(pipeline_name)
            
            # Get the appsrc element
            self.app_source = pipeline.get_by_name("audio_source")
            # Configure the appsrc properties
            self.app_source.set_property("caps", Gst.Caps.from_string(
                "audio/x-raw, format=S16LE, channels=2, rate=44100, layout=interleaved"
            ))
            self.app_source.set_property("format", Gst.Format.TIME)
            # Set to streaming mode (push-mode)
            self.app_source.set_property("stream-type", 0)  # 0 = GST_APP_STREAM_TYPE_STREAM
            
            return pipeline
            
        except Exception as e:
            print(f"Error creating playback pipeline: {e}")
            self.app_source = None
            return None
    
    def _playback_loop(self) -> None:
        """Main playback monitoring loop with improved pipeline management."""
        print("Playback monitoring loop started")
        
        last_state = None
        pipeline = None
        
        while self.running:
            try:
                # Check if state has changed
                current_state = self.playback_state
                
                if current_state != last_state:
                    print(f"State changed from {last_state} to {current_state}, recreating pipeline")
                    
                    with self.lock:
                        # Proper pipeline cleanup with state transitions
                        if pipeline:
                            print("Stopping existing pipeline...")
                            # First pause, then ready, then null (proper state machine)
                            pipeline.set_state(Gst.State.PAUSED)
                            # Wait for state change to complete with timeout
                            pipeline.get_state(Gst.CLOCK_TIME_NONE)
                            
                            pipeline.set_state(Gst.State.READY)
                            pipeline.get_state(Gst.CLOCK_TIME_NONE)
                            
                            pipeline.set_state(Gst.State.NULL)
                            pipeline.get_state(Gst.CLOCK_TIME_NONE)
                            
                            # Clear element references
                            self.app_source = None
                            pipeline = None
                            self.pipeline = None
                            
                            # Add delay to allow resources to be released
                            time.sleep(0.5)
                            
                            # Force garbage collection
                            import gc
                            gc.collect()
                        
                        # Create new pipeline if needed
                        if current_state != "none":
                            try:
                                pipeline = self._create_playback_pipeline()
                                if pipeline:
                                    print("Starting new pipeline...")
                                    # Gradual state transition: NULL -> READY -> PAUSED -> PLAYING
                                    ret = pipeline.set_state(Gst.State.READY)
                                    if ret == Gst.StateChangeReturn.FAILURE:
                                        raise Exception("Failed to reach READY state")
                                    pipeline.get_state(Gst.CLOCK_TIME_NONE)
                                    
                                    ret = pipeline.set_state(Gst.State.PAUSED)
                                    if ret == Gst.StateChangeReturn.FAILURE:
                                        raise Exception("Failed to reach PAUSED state")
                                    pipeline.get_state(Gst.CLOCK_TIME_NONE)
                                    
                                    ret = pipeline.set_state(Gst.State.PLAYING)
                                    if ret == Gst.StateChangeReturn.FAILURE:
                                        raise Exception("Failed to reach PLAYING state")
                                    
                                    # Save only after successful start
                                    self.pipeline = pipeline
                                    print(f"Successfully started playback in {current_state} mode")
                            except Exception as e:
                                print(f"Pipeline creation/start failed: {e}")
                                if pipeline:
                                    pipeline.set_state(Gst.State.NULL)
                                pipeline = None
                                self.pipeline = None
                                self.app_source = None
                        
                        last_state = current_state
                
                # Brief sleep to avoid CPU usage
                time.sleep(0.5)
                
            except Exception as e:
                print(f"Error in playback loop: {e}")
                time.sleep(1.0)
        
        # Cleanup pipeline at exit - with proper state transitions
        if pipeline:
            pipeline.set_state(Gst.State.PAUSED)
            pipeline.get_state(500 * Gst.MSECOND)  # 500ms timeout
            pipeline.set_state(Gst.State.READY)
            pipeline.get_state(500 * Gst.MSECOND)  # 500ms timeout
            pipeline.set_state(Gst.State.NULL)
        
        print("Playback monitoring loop stopped")
