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
Improved audio streaming module for the Dans le Blanc des Yeux installation.
Creates and maintains persistent pipelines for both mic types at startup,
and pauses/unpauses the appropriate pipeline based on system state.
"""

import os
import time
import threading
import socket
import configparser
from typing import Dict, Optional, Tuple, List, Callable, Any

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
CHUNK_SIZE = 1024  # Frames per buffer

# Define ports for different mic types
GLOBAL_MIC_PORT = 6000
PERSONAL_MIC_PORT = 6001

class AudioStreamer:
    """Handles audio streaming between devices using persistent GStreamer pipelines."""
    
    def __init__(self, remote_ip: str):
        self.remote_ip = remote_ip
        
        # Make ports accessible as instance variables
        self.GLOBAL_MIC_PORT = GLOBAL_MIC_PORT
        self.PERSONAL_MIC_PORT = PERSONAL_MIC_PORT
        
        # Audio device names (will be loaded from config)
        self.personal_mic_name = "TX 96Khz"
        self.global_mic_name = "USB Audio Device"
        
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
        self.threads = []
        self.lock = threading.Lock()
        
        # UDP receive sockets and threads
        self.receive_personal_socket = None
        self.receive_global_socket = None
        self.receiver_personal_thread = None
        self.receiver_global_thread = None
        
        # Callbacks for received audio
        self.on_personal_mic_received = None
        self.on_global_mic_received = None
        
        # Load settings from config
        self._load_config()
        
        # Create direct ALSA commands for finding devices (no GStreamer device monitor)
        self._find_audio_devices()
        
        # Create sockets
        self._setup_sockets()
        
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
                
                print(f"Loaded audio device names from config.ini:")
                print(f"  personal mic name: {self.personal_mic_name}")
                print(f"  global mic name: {self.global_mic_name}")
            else:
                print("No [audio] section found in config.ini, using default settings")
                
        except Exception as e:
            print(f"Error loading audio config: {e}")
            print("Using default audio device names")
    
    def _find_audio_devices(self):
        """Find audio devices using pactl command-line tool for direct PulseAudio access."""
        self.personal_mic_id = None
        self.global_mic_id = None
        
        try:
            # Use pactl to list sources
            import subprocess
            result = subprocess.run(['pactl', 'list', 'sources'], 
                                   stdout=subprocess.PIPE, 
                                   stderr=subprocess.PIPE, 
                                   text=True)
            
            if result.returncode != 0:
                raise Exception(f"pactl command failed: {result.stderr}")
            
            output = result.stdout
            
            # Parse the output to find devices
            current_device = None
            current_name = None
            devices = []
            
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
                    
                # Also look for description as backup
                elif line.startswith('Description:'):
                    description = line.split(':', 1)[1].strip()
                    # Store the description with the current device
                    if current_device:
                        devices.append((current_device, description))
            
            # Add last device if we found one
            if current_device and current_name:
                devices.append((current_device, current_name))
            
            print(f"Found {len(devices)} audio input devices:")
            for device_id, device_name in devices:
                print(f"  - {device_name} (ID: {device_id})")
                
                # Check if it matches our target devices
                if self.personal_mic_name.lower() in device_name.lower():
                    self.personal_mic_id = device_id
                    print(f"    → Matched as personal mic")
                
                if self.global_mic_name.lower() in device_name.lower():
                    self.global_mic_id = device_id
                    print(f"    → Matched as global mic")
            
            # Check if we found our devices
            if not self.personal_mic_id:
                print(f"WARNING: Could not find personal mic '{self.personal_mic_name}'")
            if not self.global_mic_id:
                print(f"WARNING: Could not find global mic '{self.global_mic_name}'")
                
        except Exception as e:
            print(f"Error discovering audio devices: {e}")
            print("Using device names as fallback")
    
    def _setup_sockets(self):
        """Set up UDP sockets for audio transmission."""
        try:
            # Create UDP sockets for receiving on both ports
            self.receive_personal_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.receive_personal_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.receive_personal_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
            self.receive_personal_socket.bind(("0.0.0.0", PERSONAL_MIC_PORT))
            self.receive_personal_socket.settimeout(0.5)  # Set a timeout for responsive shutdown
            
            self.receive_global_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.receive_global_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.receive_global_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
            self.receive_global_socket.bind(("0.0.0.0", GLOBAL_MIC_PORT))
            self.receive_global_socket.settimeout(0.5)  # Set a timeout for responsive shutdown
            
            print("UDP sockets created for audio transmission")
        except Exception as e:
            print(f"Error setting up audio sockets: {e}")
    
    def start(self) -> bool:
        """Start the audio streaming system with persistent pipelines."""
        print("Starting improved audio streamer with persistent pipelines...")
        self.running = True
        
        # Create both pipelines at startup (but initially paused)
        success = self._create_all_pipelines()
        if not success:
            print("Failed to create audio streaming pipelines")
            self.running = False
            return False
        
        # Start receiver threads for both ports
        self.receiver_personal_thread = threading.Thread(target=self._receiver_loop, 
                                                        args=(self.receive_personal_socket, True))
        self.receiver_personal_thread.daemon = True
        self.receiver_personal_thread.start()
        self.threads.append(self.receiver_personal_thread)
        
        self.receiver_global_thread = threading.Thread(target=self._receiver_loop, 
                                                      args=(self.receive_global_socket, False))
        self.receiver_global_thread.daemon = True
        self.receiver_global_thread.start()
        self.threads.append(self.receiver_global_thread)
        
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
        
        # Wait for threads to finish
        for thread in self.threads:
            if thread.is_alive():
                thread.join(timeout=1.0)
        
        # Close sockets
        for socket_obj in [self.receive_personal_socket, self.receive_global_socket]:
            if socket_obj:
                try:
                    socket_obj.close()
                except:
                    pass
        
        self.receive_personal_socket = None
        self.receive_global_socket = None
        
        print("Audio streamer stopped")
    
    def register_personal_mic_callback(self, callback: Callable[[bytes], None]) -> None:
        """Register a callback for when personal mic audio is received."""
        self.on_personal_mic_received = callback
    
    def register_global_mic_callback(self, callback: Callable[[bytes], None]) -> None:
        """Register a callback for when USB Audio Device mic audio is received."""
        self.on_global_mic_received = callback
    
    def _on_state_change(self, changed_state: str) -> None:
        """Handle system state changes."""
        if changed_state in ["local", "remote"]:
            self._update_streaming_based_on_state()
    
    def _update_streaming_based_on_state(self) -> None:
        """Update streaming state based on the current system state."""
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
            # Update personal mic pipeline
            if should_personal_be_active != self.personal_pipeline_active:
                if should_personal_be_active:
                    print("Activating personal mic streaming")
                    if self.personal_pipeline:
                        self.personal_pipeline.set_state(Gst.State.PLAYING)
                else:
                    print("Pausing personal mic streaming")
                    if self.personal_pipeline:
                        self.personal_pipeline.set_state(Gst.State.PAUSED)
                
                self.personal_pipeline_active = should_personal_be_active
            
            # Update global mic pipeline
            if should_global_be_active != self.global_pipeline_active:
                if should_global_be_active:
                    print("Activating global mic streaming")
                    if self.global_pipeline:
                        self.global_pipeline.set_state(Gst.State.PLAYING)
                else:
                    print("Pausing global mic streaming")
                    if self.global_pipeline:
                        self.global_pipeline.set_state(Gst.State.PAUSED)
                
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
                f'audio/x-raw, rate={RATE}, channels={CHANNELS} ! '
                'audioconvert ! audioresample ! '
                'audio/x-raw, format=S16LE ! '
                'udpsink host=' + self.remote_ip + ' port=' + str(port) + ' sync=false'
            )
        else:  # global
            if self.global_mic_id:
                device_param = f'device={self.global_mic_id}'
            else:
                device_param = f'device="{self.global_mic_name}"'
                
            return (
                f'pulsesrc {device_param} ! '
                f'audio/x-raw, rate={RATE}, channels={CHANNELS} ! '
                'audioconvert ! audioresample ! '
                'audio/x-raw, format=S16LE ! '
                'udpsink host=' + self.remote_ip + ' port=' + str(port) + ' sync=false'
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
            
            print("Successfully created both audio streaming pipelines")
            return True
            
        except Exception as e:
            print(f"Error creating audio streaming pipelines: {e}")
            return False
    
    def _receiver_loop(self, socket_obj, is_personal: bool) -> None:
        """Receive audio from remote device on the specified socket."""
        mic_type = "personal" if is_personal else "global"
        port = PERSONAL_MIC_PORT if is_personal else GLOBAL_MIC_PORT
        print(f"Starting {mic_type} audio receiver on port {port}")
        
        buffer = {}  # Store packets by sequence number for reordering
        next_seq = 0  # Next expected sequence number
        buffer_size = 10  # Max packets to buffer for reordering
        
        try:
            while self.running:
                try:
                    # Receive packet with timeout
                    data, addr = socket_obj.recvfrom(65536)
                    
                    if len(data) < 5:  # Need 4 bytes for seq num + 1 for mic ID
                        continue
                    
                    # Extract sequence number and mic type
                    seq_num = int.from_bytes(data[:4], byteorder='big')
                    mic_type_byte = data[4:5]
                    audio_data = data[5:]
                    
                    # Add to buffer
                    buffer[seq_num] = (mic_type_byte, audio_data)
                    
                    # Process packets in order
                    while next_seq in buffer:
                        # Get the next packet
                        mic_type_byte, audio_data = buffer.pop(next_seq)
                        
                        # Call appropriate callback based on received data
                        # and which receiver thread we're in
                        if is_personal and self.on_personal_mic_received:
                            self.on_personal_mic_received(audio_data)
                        elif not is_personal and self.on_global_mic_received:
                            self.on_global_mic_received(audio_data)
                        
                        next_seq += 1
                    
                    # Limit buffer size by dropping old packets
                    if len(buffer) > buffer_size:
                        # If buffer is too large, find the lowest sequence number
                        seq_keys = sorted(buffer.keys())
                        # Keep the most recent packets
                        for k in seq_keys[:-buffer_size]:
                            buffer.pop(k, None)
                        
                        # Update next_seq if we've skipped packets
                        if seq_keys[-buffer_size] > next_seq:
                            next_seq = seq_keys[-buffer_size]
                    
                except socket.timeout:
                    # This is expected due to the socket timeout
                    pass
                except Exception as e:
                    if self.running:
                        print(f"Error in {mic_type} audio receiver: {e}")
                        time.sleep(0.5)
        finally:
            print(f"{mic_type} audio receiver stopped")
