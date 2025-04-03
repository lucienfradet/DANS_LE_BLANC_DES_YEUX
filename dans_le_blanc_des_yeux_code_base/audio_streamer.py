"""
Audio streaming module for the Dans le Blanc des Yeux installation.
Handles capturing and sending audio streams between devices using UDP.

Fixed streaming logic:
1. When both have pressure:
   - Stream personal mic (TX) to remote device
   
2. When remote has pressure and local doesn't:
   - Stream global mic (USB) to remote device
   
3. When local has pressure and remote doesn't:
   - Stream personal mic (TX) to remote device
   
4. When neither has pressure: No streaming
"""

import os
import time
import threading
import socket
import struct
import numpy as np
import pyaudio
import wave
import configparser
from typing import Dict, Optional, Tuple, List, Callable, Any

from system_state import system_state

# Audio configuration
CHUNK_SIZE = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 2
RATE = 44100
DEVICE_SEARCH_INTERVAL = 30  # Seconds between device searches

# Network configuration
AUDIO_PORT = 6000  # Single port for audio streaming

class AudioStreamer:
    """Handles audio streaming between devices."""
    
    def __init__(self, remote_ip: str):
        self.remote_ip = remote_ip
        
        # PyAudio instance
        self.p = pyaudio.PyAudio()
        
        # Device IDs
        self.personal_mic_id = None
        self.global_mic_id = None
        
        # Audio device names (will be loaded from config)
        self.personal_mic_name = "TX 96Khz"
        self.global_mic_name = "USB Audio Device"
        
        # Load settings from config
        self._load_config()
        
        # Streaming state
        self.current_mic_sending = None  # "personal" or "global" or None
        
        # Threading
        self.running = False
        self.threads = []
        self.lock = threading.Lock()
        
        # Callbacks for received audio
        self.on_personal_mic_received = None
        self.on_global_mic_received = None
        
        # Find audio devices
        self._find_audio_devices()
        
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
                self._add_default_config_settings(config)
                
        except Exception as e:
            print(f"Error loading audio config: {e}")
            print("Using default audio device names")
    
    def _add_default_config_settings(self, config):
        """Add default audio settings to config.ini if not present"""
        try:
            if 'audio' not in config:
                config['audio'] = {}
            
            # Add device names
            if 'personal_mic_name' not in config['audio']:
                config['audio']['personal_mic_name'] = self.personal_mic_name
            if 'global_mic_name' not in config['audio']:
                config['audio']['global_mic_name'] = self.global_mic_name
            
            # Write to config file
            with open('config.ini', 'w') as configfile:
                config.write(configfile)
                
            print("Added default audio settings to config.ini")
        except Exception as e:
            print(f"Error adding default audio settings to config: {e}")
    
    def _find_audio_devices(self) -> None:
        """Find the audio devices by name."""
        # Reset device IDs
        self.personal_mic_id = None
        self.global_mic_id = None
        
        # Print all audio devices for debugging
        print("\nAudio devices available:")
        for i in range(self.p.get_device_count()):
            dev_info = self.p.get_device_info_by_index(i)
            print(f"Device {i}: {dev_info['name']}")
            print(f"  Input channels: {dev_info['maxInputChannels']}")
            print(f"  Output channels: {dev_info['maxOutputChannels']}")
            print(f"  Default sample rate: {dev_info['defaultSampleRate']}")
        
        # Find devices by name
        for i in range(self.p.get_device_count()):
            dev_info = self.p.get_device_info_by_index(i)
            
            # Check for TX personal mic device
            if self.personal_mic_name in dev_info["name"] and dev_info["maxInputChannels"] > 0:
                self.personal_mic_id = i
                print(f"Found {self.personal_mic_name} mic: Device {i}")
            
            # Check for USB Audio Device mic
            elif self.global_mic_name in dev_info["name"] and dev_info["maxInputChannels"] > 0:
                self.global_mic_id = i
                print(f"Found {self.global_mic_name} mic: Device {i}")
        
        # Fallback: any USB Audio with input channels but not personal device
        if self.global_mic_id is None:
            for i in range(self.p.get_device_count()):
                dev_info = self.p.get_device_info_by_index(i)
                if ("USB Audio" in dev_info["name"] and 
                    self.personal_mic_name not in dev_info["name"] and 
                    dev_info["maxInputChannels"] > 0):
                    self.global_mic_id = i
                    print(f"Found fallback USB mic: Device {i}")
                    break
        
        # Log the devices we found
        print(f"{self.personal_mic_name} mic ID: {self.personal_mic_id}")
        print(f"{self.global_mic_name} mic ID: {self.global_mic_id}")
        
        # If we're missing devices, warn
        missing_devices = []
        if self.personal_mic_id is None:
            missing_devices.append(f"{self.personal_mic_name} microphone")
        if self.global_mic_id is None:
            missing_devices.append(f"{self.global_mic_name} microphone")
        
        if missing_devices:
            print(f"Warning: Could not find these audio devices: {', '.join(missing_devices)}")
            print("Audio streaming may not work correctly")
        else:
            print("All required audio devices found")
    
    def start(self) -> bool:
        """Start the audio streaming system."""
        print("Starting audio streamer...")
        self.running = True
        
        # Start receiver thread
        receiver_thread = threading.Thread(target=self._receiver_loop)
        receiver_thread.daemon = True
        receiver_thread.start()
        self.threads.append(receiver_thread)
        
        # Start device monitor thread
        monitor_thread = threading.Thread(target=self._device_monitor_loop)
        monitor_thread.daemon = True
        monitor_thread.start()
        self.threads.append(monitor_thread)
        
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
            thread.join(timeout=1.0)
        
        # Clean up PyAudio
        if self.p:
            self.p.terminate()
        
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
    
    def _device_monitor_loop(self) -> None:
        """Periodically check for audio devices in case they disconnect/reconnect."""
        last_check_time = 0
        
        while self.running:
            current_time = time.time()
            
            # Check devices periodically
            if current_time - last_check_time > DEVICE_SEARCH_INTERVAL:
                print("Checking audio devices...")
                self._find_audio_devices()
                last_check_time = current_time
            
            # Sleep to avoid consuming CPU
            time.sleep(5)
    
    def _start_streaming(self, mic_type: str) -> bool:
        """Start streaming from specified mic to remote device.
        
        Args:
            mic_type: Either "personal" for TX mic or "global" for USB mic
        """
        # If already streaming the correct mic, do nothing
        if self.current_mic_sending == mic_type:
            return True
            
        # Stop any current streaming
        self._stop_streaming()
        
        if mic_type == "personal":
            if self.personal_mic_id is None:
                print("Personal mic (TX) not available")
                return False
                
            mic_id = self.personal_mic_id
            mic_name = self.personal_mic_name
        else:  # global
            if self.global_mic_id is None:
                print("Global mic (USB) not available")
                return False
                
            mic_id = self.global_mic_id
            mic_name = self.global_mic_name
        
        try:
            # Start sender thread
            sender_thread = threading.Thread(
                target=self._sender_loop, 
                args=(mic_id, mic_type)
            )
            sender_thread.daemon = True
            sender_thread.start()
            self.threads.append(sender_thread)
            
            self.current_mic_sending = mic_type
            print(f"Started {mic_name} stream to {self.remote_ip}:{AUDIO_PORT}")
            return True
        except Exception as e:
            print(f"Failed to start {mic_name} stream: {e}")
            return False
    
    def _stop_streaming(self) -> None:
        """Stop all active streaming."""
        self.current_mic_sending = None
        print("All audio streams stopped")
    
    def _create_udp_socket(self) -> socket.socket:
        """Create a UDP socket for sending audio."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
        return sock
    
    def _create_receiver_socket(self) -> socket.socket:
        """Create a UDP socket for receiving audio."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        sock.bind(("0.0.0.0", AUDIO_PORT))
        sock.settimeout(0.5)  # Set a timeout for responsive shutdown
        return sock
    
    def _sender_loop(self, mic_id: int, mic_type: str) -> None:
        """Send audio from specified mic to remote device."""
        if mic_id is None:
            print(f"{mic_type} mic not available")
            self.current_mic_sending = None
            return
        
        print(f"Starting {mic_type} mic sender to {self.remote_ip}:{AUDIO_PORT}")
        
        sock = self._create_udp_socket()
        stream = None
        
        try:
            # Get device info to check actual channel count
            device_info = self.p.get_device_info_by_index(mic_id)
            input_channels = int(device_info.get('maxInputChannels', 1))
            
            print(f"{mic_type.capitalize()} mic has {input_channels} input channels")
            
            # Create PyAudio stream for mic with correct channel count
            stream = self.p.open(
                format=FORMAT,
                channels=input_channels,  # Use actual channel count from device
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
                input_device_index=mic_id
            )
            
            seq_num = 0
            
            while self.running and self.current_mic_sending == mic_type:
                try:
                    # Read audio data
                    data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                    
                    # If input is mono but we need stereo for output, convert mono to stereo
                    if input_channels == 1 and CHANNELS == 2:
                        # Convert mono to stereo by duplicating each sample
                        mono_data = np.frombuffer(data, dtype=np.int16)
                        stereo_data = np.repeat(mono_data, 2)
                        data = stereo_data.tobytes()
                    
                    # Add mic type identifier (0 for personal, 1 for global)
                    mic_id_byte = b'\x00' if mic_type == "personal" else b'\x01'
                    
                    # Create packet with sequence number and mic type
                    packet = struct.pack(">I", seq_num) + mic_id_byte + data
                    
                    # Send packet
                    sock.sendto(packet, (self.remote_ip, AUDIO_PORT))
                    
                    seq_num += 1
                    
                except Exception as e:
                    if self.running and self.current_mic_sending == mic_type:
                        print(f"Error in {mic_type} mic sender: {e}")
                        time.sleep(1.0)
        finally:
            if stream:
                stream.stop_stream()
                stream.close()
            sock.close()
            print(f"{mic_type} mic sender stopped")
    
    def _receiver_loop(self) -> None:
        """Receive audio from remote device."""
        print(f"Starting audio receiver on port {AUDIO_PORT}")
        sock = self._create_receiver_socket()
        
        buffer = {}  # Store packets by sequence number for reordering
        next_seq = 0  # Next expected sequence number
        buffer_size = 10  # Max packets to buffer for reordering
        
        try:
            while self.running:
                try:
                    # Receive packet with sequence number
                    data, addr = sock.recvfrom(65536)
                    
                    if len(data) < 5:  # Need 4 bytes for seq num + 1 for mic ID
                        continue
                    
                    # First 4 bytes contain the sequence number
                    seq_num = struct.unpack(">I", data[:4])[0]
                    
                    # Next byte indicates mic type (0=personal, 1=global)
                    mic_type_byte = data[4:5]
                    
                    # Rest of the packet contains the audio data
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
                        time.sleep(1.0)
        finally:
            sock.close()
            print("Audio receiver stopped")


# Test function to run the audio streamer standalone
def test_audio_streamer():
    """Test the audio streamer with loopback."""
    
    # Set up system state for testing
    system_state.update_local_state({"pressure": False})
    system_state.update_remote_state({"pressure": True, "connected": True})
    
    # Initialize audio streamer with loopback address for testing
    audio_streamer = AudioStreamer("127.0.0.1")
    
    # Register callbacks for received audio
    def on_personal_mic_audio(data):
        print(f"Received personal mic audio: {len(data)} bytes")
    
    def on_global_mic_audio(data):
        print(f"Received global mic audio: {len(data)} bytes")
    
    audio_streamer.register_personal_mic_callback(on_personal_mic_audio)
    audio_streamer.register_global_mic_callback(on_global_mic_audio)
    
    audio_streamer.start()
    
    try:
        print("\nTesting different pressure states:")
        print("\n1. Remote pressure, local no pressure - Streaming GLOBAL mic")
        time.sleep(5)
        
        print("\n2. Both have pressure - Streaming PERSONAL mic")
        system_state.update_local_state({"pressure": True})
        time.sleep(5)
        
        print("\n3. Local pressure, remote no pressure - Streaming PERSONAL mic")
        system_state.update_remote_state({"pressure": False})
        time.sleep(5)
        
        print("\n4. Neither has pressure - No streaming")
        system_state.update_local_state({"pressure": False})
        time.sleep(5)
        
        print("\nAudio streaming test complete.")
        
    except KeyboardInterrupt:
        print("Test interrupted by user")
    finally:
        audio_streamer.stop()


# Run test if executed directly
if __name__ == "__main__":
    test_audio_streamer()
