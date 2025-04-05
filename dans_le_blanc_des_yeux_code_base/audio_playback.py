"""
Audio playback module for the Dans le Blanc des Yeux installation using GStreamer.
Handles playing audio with channel muting based on system state.

Fixed playback logic:
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

import os
import time
import threading
import configparser
from typing import Dict, Optional, Tuple, List, Callable, Any

# Import GStreamer
import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstAudio', '1.0')
from gi.repository import Gst, GstAudio, GLib

from system_state import system_state
from audio_streamer import AudioStreamer

# Initialize GStreamer
Gst.init(None)

# Audio configuration
RATE = 44100
CHANNELS = 2
MAX_RETRY_ATTEMPTS = 5
RETRY_DELAY = 2  # Seconds between retries
DEVICE_SEARCH_INTERVAL = 30  # Seconds between device searches

class AudioPlayback:
    """Handles audio playback with channel muting based on system state using GStreamer."""
    
    def __init__(self, audio_streamer: AudioStreamer):
        self.audio_streamer = audio_streamer
        
        # Audio output device name - Single output
        self.speaker_name = None
        self.speaker_device = None
        
        # GStreamer pipelines for playback
        self.playback_pipeline = None
        
        # Current playback state
        self.playback_state = "none"  # "none", "mute_left", "mute_right"
        
        # Threading
        self.running = False
        self.threads = []
        self.lock = threading.Lock()
        
        # Device monitoring
        self.device_monitor_thread = None
        
        # Retry counter
        self.playback_retries = 0
        
        # Register for audio callbacks - both go to same buffer now
        self.audio_streamer.register_personal_mic_callback(self._on_received_audio)
        self.audio_streamer.register_global_mic_callback(self._on_received_audio)
        
        # Find audio output device
        self._find_audio_devices()
        
        # Register as observer for state changes
        system_state.add_observer(self._on_state_change)
        
        print("Audio playback initialized")
    
    def _find_audio_devices(self) -> None:
        """Find the audio output device using GStreamer."""
        # Store old device to check for changes
        old_speaker_device = self.speaker_device
        self.speaker_device = None
        
        try:
            # Create a device monitor to enumerate audio devices
            device_monitor = Gst.DeviceMonitor.new()
            device_monitor.add_filter("Audio/Sink", None)  # Filter for audio sinks
            device_monitor.start()
            
            # Get all audio sink devices
            devices = device_monitor.get_devices()
            device_monitor.stop()
            
            print("\nAudio output devices available:")
            for i, device in enumerate(devices):
                properties = device.get_properties()
                device_class = properties.get_string("device.class") if properties.contains("device.class") else "unknown"
                
                # Get the display name and ALSA device path
                display_name = device.get_display_name()
                alsa_path = None
                
                # Try different property names for the ALSA device path
                for prop_name in ["device.path", "alsa.device", "alsa.name"]:
                    if properties.contains(prop_name):
                        alsa_path = properties.get_string(prop_name)
                        if alsa_path:
                            break
                
                print(f"Output Device {i}: {display_name}")
                print(f"  Class: {device_class}")
                print(f"  ALSA path: {alsa_path}")
                
                # Get the personal mic name from audio_streamer
                personal_mic_name = self.audio_streamer.personal_mic_name
                
                # First try to find personal output device
                if personal_mic_name in display_name and alsa_path:
                    self.speaker_name = display_name
                    self.speaker_device = alsa_path
                    print(f"Found {personal_mic_name} speaker: {alsa_path}")
                    break
            
            # If we didn't find the specific device, use the default output
            if self.speaker_device is None:
                # Try to use the default sink
                for device in devices:
                    # Check if it's a default sink
                    properties = device.get_properties()
                    is_default = False
                    
                    if properties.contains("is-default"):
                        is_default = properties.get_boolean("is-default")
                    elif "default" in device.get_display_name().lower():
                        is_default = True
                    
                    if is_default:
                        for prop_name in ["device.path", "alsa.device", "alsa.name"]:
                            if properties.contains(prop_name):
                                alsa_path = properties.get_string(prop_name)
                                if alsa_path:
                                    self.speaker_name = device.get_display_name()
                                    self.speaker_device = alsa_path
                                    print(f"Using default output device: {self.speaker_name} ({self.speaker_device})")
                                    break
                        if self.speaker_device:
                            break
                
                # If still not found, just use the first sink available
                if self.speaker_device is None and devices:
                    device = devices[0]
                    properties = device.get_properties()
                    for prop_name in ["device.path", "alsa.device", "alsa.name"]:
                        if properties.contains(prop_name):
                            alsa_path = properties.get_string(prop_name)
                            if alsa_path:
                                self.speaker_name = device.get_display_name()
                                self.speaker_device = alsa_path
                                print(f"Using first available output device: {self.speaker_name} ({self.speaker_device})")
                                break
            
            if self.speaker_device is None:
                print("Warning: Could not find any audio output device")
                print("Audio playback may not work correctly")
            else:
                # Reset retry counter if device is found
                self.playback_retries = 0
            
            # If the device changed, restart playback
            if old_speaker_device != self.speaker_device and self.running:
                print("Audio output device changed, restarting playback...")
                self._restart_playback()
            
        except Exception as e:
            print(f"Error finding audio output devices: {e}")
    
    def start(self) -> bool:
        """Start the audio playback system."""
        print("Starting audio playback...")
        self.running = True
        
        # Start device monitor thread
        self.device_monitor_thread = threading.Thread(target=self._device_monitor_loop)
        self.device_monitor_thread.daemon = True
        self.device_monitor_thread.start()
        self.threads.append(self.device_monitor_thread)
        
        # Update the playback state based on current system state
        self._update_playback_state()
        
        print("Audio playback started")
        return True
    
    def stop(self) -> None:
        """Stop the audio playback and clean up."""
        print("Stopping audio playback...")
        self.running = False
        
        # Stop any active playback
        self._stop_playback()
        
        # Wait for threads to finish
        for thread in self.threads:
            if thread.is_alive():
                thread.join(timeout=1.0)
        
        print("Audio playback stopped")
    
    def _device_monitor_loop(self) -> None:
        """Periodically check for audio devices in case they disconnect/reconnect."""
        last_check_time = 0
        
        while self.running:
            current_time = time.time()
            
            # Check devices periodically
            if current_time - last_check_time > DEVICE_SEARCH_INTERVAL:
                print("Checking audio output devices...")
                self._find_audio_devices()
                last_check_time = current_time
            
            # Sleep to avoid consuming CPU
            time.sleep(5)
    
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
            self._restart_playback()
            
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
        # We don't need to do anything here with GStreamer as it's handled directly
        # by the pipelines, but we keep the callback for compatibility
        pass
    
    def _create_playback_pipeline(self) -> Optional[Gst.Pipeline]:
        """Create a GStreamer pipeline for audio playback with channel muting."""
        try:
            if not self.speaker_device:
                print("No audio output device available, retrying...")
                if self.playback_retries < MAX_RETRY_ATTEMPTS:
                    self.playback_retries += 1
                    time.sleep(RETRY_DELAY)
                    self._find_audio_devices()
                    if not self.speaker_device:
                        print(f"Still can't find audio output device after retry {self.playback_retries}/{MAX_RETRY_ATTEMPTS}")
                        return None
                else:
                    print("Max retries reached for audio output device")
                    return None
            
            # Create a unique pipeline name
            pipeline_name = f"playback_pipeline_{int(time.time())}"
            
            # We'll create different pipelines based on the playback state
            if self.playback_state == "none":
                # No playback needed
                return None
            
            # Start with udpsrc for receiving RTP audio
            pipeline_str = (
                f"udpsrc port={self.audio_streamer.AUDIO_PORT} ! "
                "queue max-size-bytes=65536 ! "
                f"application/x-rtp,media=audio,clock-rate={RATE},encoding-name=L16,channels={CHANNELS} ! "
                "rtpL16depay ! "
                "audioconvert ! "
                "audioresample ! "
                "audio/x-raw, format=S16LE, channels=2 ! "
            )
            
            # Add appropriate channel muting filter
            if self.playback_state == "mute_left":
                # Use audiochannelmix to mute the left channel
                pipeline_str += (
                    "audiochannelmix in-channels=2 out-channels=2 matrix=\"{ 0.0, 0.0, 1.0, 1.0 }\" ! "
                )
            elif self.playback_state == "mute_right":
                # Use audiochannelmix to mute the right channel
                pipeline_str += (
                    "audiochannelmix in-channels=2 out-channels=2 matrix=\"{ 1.0, 1.0, 0.0, 0.0 }\" ! "
                )
            
            # Add the alsasink with device specification
            # We'll use device= for the ALSA device if it's a direct path
            device_spec = f"device=\"{self.speaker_device}\"" if self.speaker_device.startswith("/") else f"device={self.speaker_device}"
            
            pipeline_str += (
                f"alsasink {device_spec} sync=false buffer-time=50000"
            )
            
            print(f"Creating playback pipeline: {pipeline_str}")
            
            pipeline = Gst.parse_launch(pipeline_str)
            pipeline.set_name(pipeline_name)
            
            # Add message handlers for errors, warnings, and EOS
            bus = pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message::error", self._on_pipeline_error)
            bus.connect("message::warning", self._on_pipeline_warning)
            bus.connect("message::eos", self._on_pipeline_eos)
            
            return pipeline
            
        except Exception as e:
            print(f"Error creating playback pipeline: {e}")
            return None
    
    def _on_pipeline_error(self, bus, message):
        """Handle pipeline errors."""
        err, debug = message.parse_error()
        print(f"Error in playback pipeline: {err.message}")
        print(f"Debug info: {debug}")
        
        # If we get an error, retry after a delay
        if self.playback_retries < MAX_RETRY_ATTEMPTS:
            print(f"Retrying playback pipeline in {RETRY_DELAY} seconds...")
            self.playback_retries += 1
            threading.Timer(RETRY_DELAY, self._restart_playback).start()
        else:
            print("Max retries reached for playback pipeline")
            self._stop_playback()
    
    def _on_pipeline_warning(self, bus, message):
        """Handle pipeline warnings."""
        warn, debug = message.parse_warning()
        print(f"Warning in playback pipeline: {warn.message}")
        print(f"Debug info: {debug}")
    
    def _on_pipeline_eos(self, bus, message):
        """Handle pipeline end-of-stream."""
        print(f"End of stream in playback pipeline")
        
        # Restart playback on EOS
        self._restart_playback()
    
    def _start_playback(self) -> bool:
        """Start the audio playback with current state."""
        try:
            # Only proceed if we have a valid playback state
            if self.playback_state == "none":
                print("No playback needed in current state")
                return True
            
            # Create playback pipeline
            pipeline = self._create_playback_pipeline()
            if not pipeline:
                print("Failed to create playback pipeline")
                return False
            
            self.playback_pipeline = pipeline
            result = self.playback_pipeline.set_state(Gst.State.PLAYING)
            
            if result == Gst.StateChangeReturn.FAILURE:
                print("Failed to start playback pipeline")
                self._stop_playback()
                return False
            
            print(f"Started audio playback with {self.playback_state} mode")
            
            # Reset retry counter on success
            self.playback_retries = 0
            
            return True
            
        except Exception as e:
            print(f"Error starting playback: {e}")
            self._stop_playback()
            return False
    
    def _stop_playback(self) -> None:
        """Stop the audio playback pipeline."""
        if self.playback_pipeline:
            try:
                # Set state to NULL for clean shutdown
                self.playback_pipeline.set_state(Gst.State.NULL)
                
                # Give time for the pipeline to shut down
                time.sleep(0.2)
                
                # Clear the pipeline
                self.playback_pipeline = None
                print("Playback pipeline stopped")
            except Exception as e:
                print(f"Error stopping playback pipeline: {e}")
                # Try to clear it anyway
                self.playback_pipeline = None
    
    def _restart_playback(self) -> None:
        """Restart the audio playback based on current state."""
        # First stop any existing playback
        self._stop_playback()
        
        # Wait briefly to ensure resources are released
        time.sleep(0.5)
        
        # Start new playback if needed
        if self.playback_state != "none":
            self._start_playback()
        else:
            print("No playback needed in current state")


# Test function for the audio playback
def test_audio_playback():
    """Test the audio playback system."""
    from audio_streamer import AudioStreamer
    
    # Set up system state for testing
    system_state.update_local_state({"pressure": False})
    system_state.update_remote_state({"pressure": True, "connected": True})
    
    # Initialize audio streamer
    audio_streamer = AudioStreamer("127.0.0.1")
    audio_streamer.start()
    
    # Initialize audio playback
    audio_playback = AudioPlayback(audio_streamer)
    audio_playback.start()
    
    try:
        print("\nTesting different pressure states:")
        print("\n1. Remote pressure, local no pressure - RIGHT channel muted")
        time.sleep(5)
        
        print("\n2. Both have pressure - LEFT channel muted")
        system_state.update_local_state({"pressure": True})
        time.sleep(5)
        
        print("\n3. Local pressure, remote no pressure - LEFT channel muted")
        system_state.update_remote_state({"pressure": False})
        time.sleep(5)
        
        print("\n4. Neither has pressure - No playback")
        system_state.update_local_state({"pressure": False})
        time.sleep(5)
        
        print("\nAudio playback test complete.")
        
    except KeyboardInterrupt:
        print("Test interrupted by user")
    finally:
        # Stop in the correct order: first playback, then streamer
        audio_playback.stop()
        audio_streamer.stop()


# Run test if executed directly
if __name__ == "__main__":
    test_audio_playback()
