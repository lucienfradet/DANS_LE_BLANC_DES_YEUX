"""
Audio streaming module for the Dans le Blanc des Yeux installation using GStreamer.
Handles capturing and sending audio streams between devices using GStreamer pipelines.

streaming logic:
1. When both have pressure:
   - Stream personal mic (TX) to remote device
   
2. When remote has pressure and local doesn't:
   - Stream global mic (USB) to remote device
   
3. When local has pressure and remote doesn't:
   - Stream personal mic (TX) to remote device
   
4. When neither has pressure: No streaming (both pipelines paused)
"""

"""
Audio streaming module for the Dans le Blanc des Yeux installation using GStreamer.
Modified to use raw UDP audio data instead of RTP for more reliable transmission.
"""

import os
import time
import threading
import configparser
from typing import Dict, Optional, Tuple, List, Callable, Any
import subprocess

# Import GStreamer but avoid GLib main loop
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

from system_state import system_state

# Initialize GStreamer without relying on a main loop
Gst.init(None)

# Audio configuration
RATE = 44100
CHANNELS = 2

# Define ports for different mic types
GLOBAL_MIC_PORT = 6000
PERSONAL_MIC_PORT = 6001

class AudioStreamer:
    """Handles audio streaming between devices using persistent GStreamer pipelines with raw UDP."""
    
    def __init__(self, remote_ip: str):
        self.remote_ip = remote_ip
        
        # Make ports accessible as instance variables
        self.GLOBAL_MIC_PORT = GLOBAL_MIC_PORT
        self.PERSONAL_MIC_PORT = PERSONAL_MIC_PORT
        
        # Audio device names (will be loaded from config)
        self.personal_mic_name = "TX 96Khz"
        self.global_mic_name = "USB Audio Device"
        
        # Gain settings (will be loaded from config)
        self.personal_mic_gain = 65  # Default value
        self.global_mic_gain = 75    # Default value
        
        # Streaming state
        self.current_mic_sending = None  # "personal" or "global" or None
        
        # Pipeline objects
        self.personal_pipeline = None
        self.global_pipeline = None
        
        # Pipeline states
        self.personal_pipeline_active = False
        self.global_pipeline_active = False
        
        # Threading
        self.running = False
        self.lock = threading.Lock()
        
        # Load settings from config
        self._load_config()
        
        # Create direct ALSA commands for finding devices (no GStreamer device monitor)
        self._find_audio_devices()
        
        # Set microphone gain levels
        self._set_mic_gains()
        
        # Register as observer for state changes
        system_state.add_observer(self._on_state_change)
        
        print(f"Audio streamer initialized with remote IP: {remote_ip}")
    
    def _load_config(self):
        """Load audio settings from config.ini"""
        try:
            config = configparser.ConfigParser()
            config.read('config.ini')
            
            if 'audio' in config:
                # Load device names
                self.personal_mic_name = config.get('audio', 'personal_mic_name', fallback="TX 96Khz")
                self.global_mic_name = config.get('audio', 'global_mic_name', fallback="USB Audio Device")
                
                # Load gain settings
                self.personal_mic_gain = config.getint('audio', 'personal_mic_gain', fallback=65)
                self.global_mic_gain = config.getint('audio', 'global_mic_gain', fallback=75)
                
                print(f"Loaded audio device names from config.ini:")
                print(f"  personal mic name: {self.personal_mic_name}")
                print(f"  global mic name: {self.global_mic_name}")
                print(f"  personal mic gain: {self.personal_mic_gain}")
                print(f"  global mic gain: {self.global_mic_gain}")
            else:
                print("No [audio] section found in config.ini, using default settings")
                
        except Exception as e:
            print(f"Error loading audio config: {e}")
            print("Using default audio device names and gain settings")
    
    def _find_audio_devices(self):
        """Find audio devices using pactl command-line tool with flexible name matching and retries."""
        self.personal_mic_id = None
        self.global_mic_id = None
        
        max_attempts = 3  # Try up to 3 times
        retry_delay = 3   # Wait 3 seconds between attempts
        
        for attempt in range(1, max_attempts + 1):
            try:
                print(f"Discovering audio devices (attempt {attempt}/{max_attempts})...")
                
                # Use pactl to list sources
                result = subprocess.run(['pactl', 'list', 'sources'], 
                                       stdout=subprocess.PIPE, 
                                       stderr=subprocess.PIPE, 
                                       text=True)
                
                if result.returncode != 0:
                    raise Exception(f"pactl command failed: {result.stderr}")
                
                output = result.stdout
                
                # Parse the output to find devices
                devices = []  # List of (device_id, name) tuples
                current_device = None
                current_name = None
                
                for line in output.split('\n'):
                    line = line.strip()
                    
                    # New source entry
                    if line.startswith('Source #'):
                        # Save previous device if we found one
                        if current_device and current_name:
                            devices.append((current_device, current_name))
                        
                        # Extract source number
                        current_device = line.split('#')[1].strip()
                        current_name = None
                        
                    # Get the device name
                    elif line.startswith('Name:'):
                        current_name = line.split(':', 1)[1].strip()
                
                # Add last device if we found one
                if current_device and current_name:
                    devices.append((current_device, current_name))
                
                print(f"Found {len(devices)} audio input devices:")
                
                for device_id, device_name in devices:
                    print(f"  - {device_name} (ID: {device_id})")
                    
                    # Try to match personal mic
                    if not self.personal_mic_id:
                        # First try exact match
                        if device_name == self.personal_mic_name:
                            self.personal_mic_id = device_id
                            print(f"    → Matched as personal mic (exact match)")
                        else:
                            # Try matching base name (remove numeric suffix if any)
                            configured_base = self._get_base_name(self.personal_mic_name)
                            device_base = self._get_base_name(device_name)
                            
                            # Check if the base names match
                            if configured_base == device_base:
                                self.personal_mic_id = device_id
                                print(f"    → Matched as personal mic (base name match: {configured_base})")
                    
                    # Try to match global mic
                    if not self.global_mic_id:
                        # First try exact match
                        if device_name == self.global_mic_name:
                            self.global_mic_id = device_id
                            print(f"    → Matched as global mic (exact match)")
                        else:
                            # Try matching base name (remove numeric suffix if any)
                            configured_base = self._get_base_name(self.global_mic_name)
                            device_base = self._get_base_name(device_name)
                            
                            # Check if the base names match
                            if configured_base == device_base:
                                self.global_mic_id = device_id
                                print(f"    → Matched as global mic (base name match: {configured_base})")
                
                # If we found both devices, we're done
                if self.personal_mic_id and self.global_mic_id:
                    print("Successfully found both audio devices")
                    return
                    
                # If we found at least one device, we're making progress
                if self.personal_mic_id or self.global_mic_id:
                    print("Found at least one required audio device")
                    
                # If we didn't find any devices and have more attempts, wait and retry
                if not (self.personal_mic_id or self.global_mic_id) and attempt < max_attempts:
                    print(f"No required audio devices found, waiting {retry_delay} seconds before retry...")
                    time.sleep(retry_delay)
                    continue
                    
                # Check if we found our devices after all attempts
                if not self.personal_mic_id:
                    print(f"WARNING: Could not find personal mic with name matching '{self.personal_mic_name}'")
                if not self.global_mic_id:
                    print(f"WARNING: Could not find global mic with name matching '{self.global_mic_name}'")
                    
            except Exception as e:
                print(f"Error discovering audio devices (attempt {attempt}/{max_attempts}): {e}")
                
                # If we have more attempts, wait and retry
                if attempt < max_attempts:
                    print(f"Waiting {retry_delay} seconds before retry...")
                    time.sleep(retry_delay)
                else:
                    print("Using device names as fallback")
        
    def _get_base_name(self, name):
        """Get base name by removing numeric suffix if present."""
        # Check if name ends with a numeric suffix (like .2, .3, etc.)
        parts = name.split('.')
        if len(parts) > 1 and parts[-1].isdigit():
            # Return everything except the numeric suffix
            return '.'.join(parts[:-1])
        return name

    def _set_mic_gains(self):
        """Set microphone gain levels using PulseAudio commands."""
        try:
            # Convert gain percentage (0-100) to volume level PulseAudio expects (0.0-1.0)
            personal_volume = self.personal_mic_gain / 100.0
            global_volume = self.global_mic_gain / 100.0
            
            # Format as PulseAudio expects (0x10000 = 100% = 1.0)
            personal_pa_volume = int(personal_volume * 0x10000)
            global_pa_volume = int(global_volume * 0x10000)
            
            print(f"Setting microphone gain levels:")
            
            # Set personal mic gain if found
            if self.personal_mic_id:
                cmd = ['pactl', 'set-source-volume', self.personal_mic_id, f'{personal_pa_volume}']
                result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                
                if result.returncode == 0:
                    print(f"  → Set personal mic '{self.personal_mic_name}' (ID:{self.personal_mic_id}) gain to {self.personal_mic_gain}%")
                else:
                    print(f"  → Failed to set personal mic gain: {result.stderr}")
            else:
                print(f"  → Cannot set personal mic gain: device not found")
            
            # Set global mic gain if found
            if self.global_mic_id:
                cmd = ['pactl', 'set-source-volume', self.global_mic_id, f'{global_pa_volume}']
                result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                
                if result.returncode == 0:
                    print(f"  → Set global mic '{self.global_mic_name}' (ID:{self.global_mic_id}) gain to {self.global_mic_gain}%")
                else:
                    print(f"  → Failed to set global mic gain: {result.stderr}")
            else:
                print(f"  → Cannot set global mic gain: device not found")
                
        except Exception as e:
            print(f"Error setting microphone gain levels: {e}")
    
    def start(self) -> bool:
        """Start the audio streaming system with persistent pipelines."""
        print("Starting audio streamer with raw UDP audio...")
        self.running = True
        
        # Create both pipelines at startup (but initially paused)
        success = self._create_all_pipelines()
        if not success:
            print("Failed to create audio streaming pipelines")
            self.running = False
            return False
        
        # Check initial state to update which pipeline should be active
        self._update_streaming_based_on_state()
        
        print("Audio streamer started with persistent pipelines")
        return True
    
    def stop(self) -> None:
        """Stop all streaming and release resources."""
        print("Stopping audio streamer...")
        self.running = False
        
        # Pause both pipelines
        with self.lock:
            if self.personal_pipeline:
                self.personal_pipeline.set_state(Gst.State.PAUSED)
                self.personal_pipeline.set_state(Gst.State.READY)
                self.personal_pipeline.set_state(Gst.State.NULL)
                self.personal_pipeline = None
            
            if self.global_pipeline:
                self.global_pipeline.set_state(Gst.State.PAUSED)
                self.global_pipeline.set_state(Gst.State.READY)
                self.global_pipeline.set_state(Gst.State.NULL)
                self.global_pipeline = None
        
        # Reset pipeline states
        self.personal_pipeline_active = False
        self.global_pipeline_active = False
        
        print("Audio streamer stopped")
    
    def _on_state_change(self, changed_state: str) -> None:
        """Handle system state changes."""
        if changed_state in ["local", "remote"]:
            self._update_streaming_based_on_state()
    
    def _update_streaming_based_on_state(self) -> None:
        """Update streaming state with improved state transitions."""
        local_state = system_state.get_local_state()
        remote_state = system_state.get_remote_state()
        
        # Default to all pipelines paused
        should_personal_be_active = False
        should_global_be_active = False
        
        # Only proceed if remote is connected
        if remote_state.get("connected", False):
            # Case 1: Both have pressure - stream personal mic (TX)
            if local_state.get("pressure", False) and remote_state.get("pressure", False):
                should_personal_be_active = True
            
            # Case 2: Remote has pressure but local doesn't - stream global mic (USB)
            elif remote_state.get("pressure", False) and not local_state.get("pressure", False):
                should_global_be_active = True
            
            # Case 3: Local has pressure but remote doesn't - stream personal mic (TX)
            elif local_state.get("pressure", False) and not remote_state.get("pressure", False):
                should_personal_be_active = True
        
        # Update pipeline states based on should_X_be_active flags
        with self.lock:
            # Update personal mic pipeline with proper state transitions
            if should_personal_be_active != self.personal_pipeline_active:
                if should_personal_be_active:
                    print("Activating personal mic streaming with proper state transitions")
                    if self.personal_pipeline:
                        # First to READY, then to PLAYING for better negotiation
                        self.personal_pipeline.set_state(Gst.State.READY)
                        self.personal_pipeline.get_state(500 * Gst.MSECOND)  # Wait for state change
                        ret = self.personal_pipeline.set_state(Gst.State.PLAYING)
                        
                        # Check if state change succeeded
                        if ret == Gst.StateChangeReturn.FAILURE:
                            print("Failed to set personal pipeline to PLAYING, recreating pipeline")
                            # Try to recreate the pipeline
                            self.personal_pipeline.set_state(Gst.State.NULL)
                            personal_pipeline_str = self._create_pipeline_str("personal", self.PERSONAL_MIC_PORT)
                            self.personal_pipeline = Gst.parse_launch(personal_pipeline_str)
                            self.personal_pipeline.set_state(Gst.State.PLAYING)
                else:
                    print("Pausing personal mic streaming")
                    if self.personal_pipeline:
                        # First to PAUSED, then to READY to keep resources available
                        self.personal_pipeline.set_state(Gst.State.PAUSED)
                        self.personal_pipeline.get_state(500 * Gst.MSECOND)  # Wait for state change
                        self.personal_pipeline.set_state(Gst.State.READY)
                
                self.personal_pipeline_active = should_personal_be_active
            
            # Update global mic pipeline
            if should_global_be_active != self.global_pipeline_active:
                if should_global_be_active:
                    print("Activating global mic streaming with proper state transitions")
                    if self.global_pipeline:
                        # Same careful state transitions
                        self.global_pipeline.set_state(Gst.State.READY)
                        self.global_pipeline.get_state(500 * Gst.MSECOND)  # Wait for state change
                        ret = self.global_pipeline.set_state(Gst.State.PLAYING)
                        
                        if ret == Gst.StateChangeReturn.FAILURE:
                            print("Failed to set global pipeline to PLAYING, recreating pipeline")
                            # Try to recreate the pipeline
                            self.global_pipeline.set_state(Gst.State.NULL)
                            global_pipeline_str = self._create_pipeline_str("global", self.GLOBAL_MIC_PORT)
                            self.global_pipeline = Gst.parse_launch(global_pipeline_str)
                            self.global_pipeline.set_state(Gst.State.PLAYING)
                else:
                    print("Pausing global mic streaming")
                    if self.global_pipeline:
                        self.global_pipeline.set_state(Gst.State.PAUSED)
                        self.global_pipeline.get_state(500 * Gst.MSECOND)  # Wait for state change
                        self.global_pipeline.set_state(Gst.State.READY)
                
                self.global_pipeline_active = should_global_be_active
        
        # Update system state with audio info
        system_state.update_audio_state({
            "audio_sending": should_personal_be_active or should_global_be_active,
            "audio_mic": "personal" if should_personal_be_active else 
                         "global" if should_global_be_active else "None"
        })
    
    def _create_pipeline_str(self, mic_type: str, port: int) -> str:
        """Create a pipeline string for the specified mic type using device IDs when available."""
        if mic_type == "personal":
            # For PulseAudio, we can use the device number directly
            if self.personal_mic_id:
                device_param = f'device={self.personal_mic_id}'
            else:
                device_param = f'device="{self.personal_mic_name}"'
                
            return (
                f'pulsesrc {device_param} ! '
                'audio/x-raw, rate=44100, channels=1 ! '  # Explicitly set mono input
                'audioconvert ! '
                'audioresample ! '
                'audio/x-raw, format=S16LE, channels=2, rate=44100 ! '  # Convert to stereo
                'audioconvert ! '
                'rtpL24pay ! '  # RTP with L24
                f'udpsink host={self.remote_ip} port={port} sync=false buffer-size=65536'
            )
        else:  # global
            # Similar changes for global mic
            if self.global_mic_id:
                device_param = f'device={self.global_mic_id}'
            else:
                device_param = f'device="{self.global_mic_name}"'
                
            return (
                f'pulsesrc {device_param} ! '
                f'audio/x-raw, rate={RATE}, channels={CHANNELS} ! '
                'audioconvert ! audioresample ! '
                'audio/x-raw, format=S16LE, channels=2, rate=44100 ! '
                'audioconvert ! '
                'rtpL24pay ! '  # RTP with L24
                f'udpsink host={self.remote_ip} port={port} sync=false buffer-size=65536'
            )
    
    def _create_all_pipelines(self) -> bool:
        """Create both streaming pipelines but initially set them to PAUSED state."""
        try:
            # Create personal mic pipeline
            personal_pipeline_str = self._create_pipeline_str("personal", PERSONAL_MIC_PORT)
            print(f"Creating personal mic pipeline: {personal_pipeline_str}")
            self.personal_pipeline = Gst.parse_launch(personal_pipeline_str)
            
            # Set to READY state initially (prepare but don't start)
            ret = self.personal_pipeline.set_state(Gst.State.READY)
            if ret == Gst.StateChangeReturn.FAILURE:
                print("Failed to set personal mic pipeline to READY state")
                return False
            
            # Create global mic pipeline
            global_pipeline_str = self._create_pipeline_str("global", GLOBAL_MIC_PORT)
            print(f"Creating global mic pipeline: {global_pipeline_str}")
            self.global_pipeline = Gst.parse_launch(global_pipeline_str)
            
            # Set to READY state initially (prepare but don't start)
            ret = self.global_pipeline.set_state(Gst.State.READY)
            if ret == Gst.StateChangeReturn.FAILURE:
                print("Failed to set global mic pipeline to READY state")
                return False
            
            print("Successfully created both audio streaming pipelines using WAV format")
            return True
            
        except Exception as e:
            print(f"Error creating audio streaming pipelines: {e}")
            return False
