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
   
4. When neither has pressure: No streaming
"""

"""
Minimal audio streaming module for the Dans le Blanc des Yeux installation.
Uses direct GStreamer pipeline with NO GLib main loop to avoid X11/OpenCV conflicts.
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
AUDIO_PORT = 6000  # Base port for audio streaming

class AudioStreamer:
    """Handles audio streaming between devices using minimal GStreamer pipelines."""
    
    def __init__(self, remote_ip: str):
        self.remote_ip = remote_ip
        
        # Make AUDIO_PORT accessible as an instance variable for AudioPlayback to reference
        self.AUDIO_PORT = AUDIO_PORT
        
        # Audio device names (will be loaded from config)
        self.personal_mic_name = "TX 96Khz"
        self.global_mic_name = "USB Audio Device"
        
        # Streaming state
        self.current_mic_sending = None  # "personal" or "global" or None
        
        # Threading
        self.running = False
        self.threads = []
        self.lock = threading.Lock()
        
        # Socket for sending audio
        self.send_socket = None
        
        # UDP receive socket and thread
        self.receive_socket = None
        self.receiver_thread = None
        
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
        """Find audio devices and map names to IDs for more reliable access."""
        self.personal_mic_id = None
        self.global_mic_id = None
        
        try:
            # Create a temporary pipeline to enumerate devices
            device_list_pipeline = Gst.parse_launch("pulsesrc name=source ! fakesink")
            source = device_list_pipeline.get_by_name("source")
            
            # Get the device property from element
            device_prop = source.get_property("device")
            
            # Get the PulseAudio device enumeration
            device_list_pipeline.set_state(Gst.State.READY)
            
            # Get available devices
            device_provider = source.get_property("device-provider")
            if device_provider:
                devices = device_provider.get_devices()
                
                print(f"Found {len(devices)} audio input devices:")
                for device in devices:
                    device_name = device.get_display_name()
                    device_id = device.get_properties().get_string("device.id")
                    
                    print(f"  - {device_name} (ID: {device_id})")
                    
                    # Check if it matches our target devices
                    if self.personal_mic_name.lower() in device_name.lower():
                        self.personal_mic_id = device_id
                        print(f"    → Matched as personal mic")
                    
                    if self.global_mic_name.lower() in device_name.lower():
                        self.global_mic_id = device_id
                        print(f"    → Matched as global mic")
            
            # Clean up
            device_list_pipeline.set_state(Gst.State.NULL)
            
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
            # Create a UDP socket for sending
            self.send_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.send_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
            
            # Create a UDP socket for receiving
            self.receive_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.receive_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.receive_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
            self.receive_socket.bind(("0.0.0.0", AUDIO_PORT))
            self.receive_socket.settimeout(0.5)  # Set a timeout for responsive shutdown
            
            print("UDP sockets created for audio transmission")
        except Exception as e:
            print(f"Error setting up audio sockets: {e}")
    
    def start(self) -> bool:
        """Start the audio streaming system."""
        print("Starting minimal audio streamer...")
        self.running = True
        
        # Start receiver thread
        self.receiver_thread = threading.Thread(target=self._receiver_loop)
        self.receiver_thread.daemon = True
        self.receiver_thread.start()
        self.threads.append(self.receiver_thread)
        
        # Check initial state to see if we need to start streaming right away
        self._update_streaming_based_on_state()
        
        print("Audio streamer started")
        return True
    
    def stop(self) -> None:
        """Stop all streaming and release resources."""
        print("Stopping audio streamer...")
        self.running = False
        
        # Stop any active streaming
        self._stop_streaming()
        
        # Wait for threads to finish
        for thread in self.threads:
            if thread.is_alive():
                thread.join(timeout=1.0)
        
        # Close sockets
        if self.send_socket:
            try:
                self.send_socket.close()
            except:
                pass
        
        if self.receive_socket:
            try:
                self.receive_socket.close()
            except:
                pass
        
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
        
        # Only proceed if remote is connected
        if not remote_state.get("connected", False):
            self._stop_streaming()
            return
        
        # Case 1: Both have pressure - stream personal mic (TX)
        if local_state.get("pressure", False) and remote_state.get("pressure", False):
            self._start_streaming("personal")
        
        # Case 2: Remote has pressure but local doesn't - stream global mic (USB)
        elif remote_state.get("pressure", False) and not local_state.get("pressure", False):
            self._start_streaming("global")
        
        # Case 3: Local has pressure but remote doesn't - stream personal mic (TX)
        elif local_state.get("pressure", False) and not remote_state.get("pressure", False):
            self._start_streaming("personal")
        
        # Case 4: No pressure on either - no streaming
        else:
            self._stop_streaming()
    
    def _create_pipeline_str(self, mic_type: str) -> str:
        """Create a pipeline string for the specified mic type using device IDs when available."""
        if mic_type == "personal":
            # Use device ID if available, otherwise fall back to name
            device_param = f'device-id="{self.personal_mic_id}"' if self.personal_mic_id else f'device="{self.personal_mic_name}"'
            return (
                f'pulsesrc {device_param} ! '
                f'audio/x-raw, rate={RATE}, channels={CHANNELS} ! '
                'audioconvert ! audioresample ! '
                'audio/x-raw, format=S16LE ! '
                'appsink name=sink emit-signals=true sync=false'
            )
        else:  # global
            device_param = f'device-id="{self.global_mic_id}"' if self.global_mic_id else f'device="{self.global_mic_name}"'
            return (
                f'pulsesrc {device_param} ! '
                f'audio/x-raw, rate={RATE}, channels={CHANNELS} ! '
                'audioconvert ! audioresample ! '
                'audio/x-raw, format=S16LE ! '
                'appsink name=sink emit-signals=true sync=false'
            )
    
    def _start_streaming(self, mic_type: str) -> bool:
        """Start streaming from specified mic to remote device using non-blocking approach."""
        # If already streaming the correct mic, do nothing
        if self.current_mic_sending == mic_type:
            return True
            
        # Stop any current streaming
        self._stop_streaming()
        
        try:
            # Start sender thread to avoid blocking the main thread
            sender_thread = threading.Thread(
                target=self._sender_loop, 
                args=(mic_type,)
            )
            sender_thread.daemon = True
            sender_thread.start()
            self.threads.append(sender_thread)
            
            self.current_mic_sending = mic_type
            print(f"Started {mic_type} mic stream to {self.remote_ip}:{AUDIO_PORT}")
            
            # Update system state with audio info
            system_state.update_audio_state({
                "audio_sending": True,
                "audio_mic": mic_type
            })
            
            return True
            
        except Exception as e:
            print(f"Error starting {mic_type} stream: {e}")
            self._stop_streaming()
            return False
    
    def _sender_loop(self, mic_type: str) -> None:
        """Capture and send audio from the specified mic type."""
        print(f"Starting {mic_type} sender loop")
        
        try:
            # Create pipeline based on mic type
            pipeline_str = self._create_pipeline_str(mic_type)
            print(f"Using pipeline: {pipeline_str}")
            
            pipeline = Gst.parse_launch(pipeline_str)
            sink = pipeline.get_by_name("sink")
            
            # Start the pipeline
            pipeline.set_state(Gst.State.PLAYING)
            
            seq_num = 0
            
            while self.running and self.current_mic_sending == mic_type:
                try:
                    # Pull a sample from the sink - using standard pull_sample
                    # with a timeout implemented using a separate check
                    sample = None
                    start_time = time.time()
                    timeout = 0.1  # 100ms timeout
                    
                    while time.time() - start_time < timeout:
                        # Non-blocking check if sample is available
                        sample = sink.emit("pull-sample")
                        if sample:
                            break
                        time.sleep(0.01)  # Small sleep to avoid busy wait
                    
                    if not sample:
                        continue
                    
                    # Extract the audio data
                    buffer = sample.get_buffer()
                    success, map_info = buffer.map(Gst.MapFlags.READ)
                    
                    if success:
                        # Get the audio data
                        audio_data = bytes(map_info.data)
                        buffer.unmap(map_info)
                        
                        # Add mic type identifier (0 for personal, 1 for global)
                        mic_id_byte = b'\x00' if mic_type == "personal" else b'\x01'
                        
                        # Add sequence number
                        packet = seq_num.to_bytes(4, byteorder='big') + mic_id_byte + audio_data
                        
                        # Send to remote
                        if self.send_socket:
                            self.send_socket.sendto(packet, (self.remote_ip, AUDIO_PORT))
                        
                        seq_num = (seq_num + 1) % 65536
                    
                except Exception as e:
                    if self.running and self.current_mic_sending == mic_type:
                        print(f"Error in {mic_type} sender loop: {e}")
                        time.sleep(0.5)
            
            # Stop the pipeline
            pipeline.set_state(Gst.State.NULL)
            
        except Exception as e:
            print(f"Error in {mic_type} sender thread: {e}")
        
        print(f"{mic_type} sender loop ended")
    
    def _stop_streaming(self) -> None:
        """Stop all active streaming."""
        old_mic = self.current_mic_sending
        self.current_mic_sending = None
        
        if old_mic:
            print("All streams stopped")
        
        # Update system state
        system_state.update_audio_state({
            "audio_sending": False,
            "audio_mic": "None"
        })
    
    def _receiver_loop(self) -> None:
        """Receive audio from remote device."""
        print(f"Starting audio receiver on port {AUDIO_PORT}")
        
        buffer = {}  # Store packets by sequence number for reordering
        next_seq = 0  # Next expected sequence number
        buffer_size = 10  # Max packets to buffer for reordering
        
        try:
            while self.running:
                try:
                    # Receive packet with timeout
                    data, addr = self.receive_socket.recvfrom(65536)
                    
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
                        
                        # Call appropriate callback based on mic type
                        if mic_type_byte == b'\x00' and self.on_personal_mic_received:
                            self.on_personal_mic_received(audio_data)
                        elif mic_type_byte == b'\x01' and self.on_global_mic_received:
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
                        print(f"Error in audio receiver: {e}")
                        time.sleep(0.5)
        finally:
            print("Audio receiver stopped")
