"""
Audio streaming module for the Dans le Blanc des Yeux installation.
Handles capturing and sending audio streams between devices using UDP.

Streaming Logic:
1. When remote device has pressure=true and local doesn't:
   - Stream USB Audio Device mic to remote device
   - Receive personal mic from remote device

2. When local device has pressure=true and remote doesn't:
   - Receive USB Audio Device mic from remote device
   - Stream personal mic to remote device

3. When both have pressure=true:
   - Stream personal mic to remote device
   - Receive personal mic from remote device

4. When neither has pressure: No streaming required
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
AUDIO_PORT_PERSONAL_MIC = 6000  # Port for personal mic audio stream
AUDIO_PORT_GLOBAL_MIC = 6001  # Port for USB Audio Device mic stream

class AudioStreamer:
    """Handles audio streaming between devices."""
    
    def __init__(self, remote_ip: str):
        self.remote_ip = remote_ip
        
        # PyAudio instance
        self.p = pyaudio.PyAudio()
        
        # Device IDs
        self.personal_mic_id = None
        self.global_mic_id = None
        self.personal_speaker_id = None
        
        # Audio device names (will be loaded from config)
        self.personal_mic_name = "TX 96Khz"
        self.global_mic_name = "USB Audio Device"
        
        # Load settings from config
        self._load_config()
        
        # Streaming state
        self.personal_mic_sending = False
        self.global_mic_sending = False
        
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
        self.personal_speaker_id = None
        
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
            
            # Check for personal mic device
            if self.personal_mic_name in dev_info["name"]:
                if dev_info["maxInputChannels"] > 0:
                    self.personal_mic_id = i
                    print(f"Found {self.personal_mic_name} mic: Device {i}")
                if dev_info["maxOutputChannels"] > 0:
                    self.personal_speaker_id = i
                    print(f"Found {self.personal_mic_name} speaker: Device {i}")
            
            # Check for USB Audio Device mic
            elif self.global_mic_name in dev_info["name"] and dev_info["maxInputChannels"] > 0:
                self.global_mic_id = i
                print(f"Found {self.global_mic_name} mic: Device {i}")
            
            # Fallback: any USB Audio with input channels but not personal device
            elif "USB Audio" in dev_info["name"] and self.personal_mic_name not in dev_info["name"] and dev_info["maxInputChannels"] > 0:
                if self.global_mic_id is None:
                    self.global_mic_id = i
                    print(f"Found fallback USB mic: Device {i}")
        
        # Log the devices we found
        print(f"{self.personal_mic_name} mic ID: {self.personal_mic_id}")
        print(f"{self.global_mic_name} mic ID: {self.global_mic_id}")
        print(f"{self.personal_mic_name} speaker ID: {self.personal_speaker_id}")
        
        # If we're missing any devices, warn
        missing_devices = []
        if self.personal_mic_id is None:
            missing_devices.append(f"{self.personal_mic_name} microphone")
        if self.global_mic_id is None:
            missing_devices.append(f"{self.global_mic_name} microphone")
        if self.personal_speaker_id is None:
            missing_devices.append(f"{self.personal_mic_name} speaker")
        
        if missing_devices:
            print(f"Warning: Could not find these audio devices: {', '.join(missing_devices)}")
            print("Audio streaming may not work correctly")
        else:
            print("All required audio devices found")
    
    def start(self) -> bool:
        """Start the audio streaming system."""
        print("Starting audio streamer...")
        self.running = True
        
        # Start receiver threads
        self._start_receiver_threads()
        
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
        self._stop_all_streams()
        
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
            self._stop_all_streams()
            return
        
        # Case 1: Both have pressure - stream personal mic in both directions
        if local_state.get("pressure", False) and remote_state.get("pressure", False):
            self._start_personal_mic_stream()
            self._stop_global_mic_stream()
        
        # Case 2: Remote has pressure but local doesn't - stream global mic and receive personal mic
        elif remote_state.get("pressure", False) and not local_state.get("pressure", False):
            self._stop_personal_mic_stream()
            self._start_global_mic_stream()
        
        # Case 3: Local has pressure but remote doesn't - stream personal mic and receive global mic
        elif local_state.get("pressure", False) and not remote_state.get("pressure", False):
            self._start_personal_mic_stream()
            self._stop_global_mic_stream()
        
        # Case 4: No pressure on either - no streaming
        else:
            self._stop_all_streams()
    
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
    
    def _start_receiver_threads(self) -> None:
        """Start threads to receive audio streams."""
        # Start personal mic receiver thread
        personal_mic_receiver = threading.Thread(target=self._personal_mic_receiver_loop)
        personal_mic_receiver.daemon = True
        personal_mic_receiver.start()
        self.threads.append(personal_mic_receiver)
        
        # Start USB Audio Device mic receiver thread
        global_mic_receiver = threading.Thread(target=self._global_mic_receiver_loop)
        global_mic_receiver.daemon = True
        global_mic_receiver.start()
        self.threads.append(global_mic_receiver)
    
    def _start_personal_mic_stream(self) -> bool:
        """Start streaming from personal mic to remote device."""
        if self.personal_mic_sending or self.personal_mic_id is None:
            return False
        
        try:
            # Start sender thread
            sender_thread = threading.Thread(target=self._personal_mic_sender_loop)
            sender_thread.daemon = True
            sender_thread.start()
            self.threads.append(sender_thread)
            
            self.personal_mic_sending = True
            print(f"Started personal mic stream to {self.remote_ip}:{AUDIO_PORT_PERSONAL_MIC}")
            return True
        except Exception as e:
            print(f"Failed to start personal mic stream: {e}")
            return False
    
    def _start_global_mic_stream(self) -> bool:
        """Start streaming from USB Audio Device mic to remote device."""
        if self.global_mic_sending or self.global_mic_id is None:
            return False
        
        try:
            # Start sender thread
            sender_thread = threading.Thread(target=self._global_mic_sender_loop)
            sender_thread.daemon = True
            sender_thread.start()
            self.threads.append(sender_thread)
            
            self.global_mic_sending = True
            print(f"Started USB Audio Device mic stream to {self.remote_ip}:{AUDIO_PORT_GLOBAL_MIC}")
            return True
        except Exception as e:
            print(f"Failed to start USB Audio Device mic stream: {e}")
            return False
    
    def _stop_personal_mic_stream(self) -> None:
        """Stop streaming from personal mic."""
        if self.personal_mic_sending:
            self.personal_mic_sending = False
            print("Stopped personal mic stream")
    
    def _stop_global_mic_stream(self) -> None:
        """Stop streaming from USB Audio Device mic."""
        if self.global_mic_sending:
            self.global_mic_sending = False
            print("Stopped USB Audio Device mic stream")
    
    def _stop_all_streams(self) -> None:
        """Stop all active streams."""
        self._stop_personal_mic_stream()
        self._stop_global_mic_stream()
        print("All audio streams stopped")
    
    def _create_udp_socket(self) -> socket.socket:
        """Create a UDP socket for sending audio."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
        return sock
    
    def _create_receiver_socket(self, port: int) -> socket.socket:
        """Create a UDP socket for receiving audio."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        sock.bind(("0.0.0.0", port))
        sock.settimeout(0.5)  # Set a timeout for responsive shutdown
        return sock
    
    def _personal_mic_sender_loop(self) -> None:
        """Send personal mic audio to remote device."""
        if self.personal_mic_id is None:
            print("personal mic not available")
            self.personal_mic_sending = False
            return
        
        print(f"Starting personal mic sender to {self.remote_ip}:{AUDIO_PORT_PERSONAL_MIC}")
        
        sock = self._create_udp_socket()
        stream = None
        
        try:
            # Get device info to check actual channel count
            device_info = self.p.get_device_info_by_index(self.personal_mic_id)
            input_channels = int(device_info.get('maxInputChannels', 1))
            
            print(f"Personal mic has {input_channels} input channels")
            
            # Create PyAudio stream for personal mic with correct channel count
            stream = self.p.open(
                format=FORMAT,
                channels=input_channels,  # Use actual channel count from device
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
                input_device_index=self.personal_mic_id
            )
            
            seq_num = 0
            
            while self.running and self.personal_mic_sending:
                try:
                    # Read audio data
                    data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                    
                    # If input is mono but we need stereo for output, convert mono to stereo
                    if input_channels == 1 and CHANNELS == 2:
                        # Convert mono to stereo by duplicating each sample
                        mono_data = np.frombuffer(data, dtype=np.int16)
                        stereo_data = np.repeat(mono_data, 2)
                        data = stereo_data.tobytes()
                    
                    # Create packet with sequence number
                    packet = struct.pack(">I", seq_num) + data
                    
                    # Send packet
                    sock.sendto(packet, (self.remote_ip, AUDIO_PORT_PERSONAL_MIC))
                    
                    seq_num += 1
                    
                except Exception as e:
                    if self.running and self.personal_mic_sending:
                        print(f"Error in personal mic sender: {e}")
                        time.sleep(1.0)
        finally:
            if stream:
                stream.stop_stream()
                stream.close()
            sock.close()
            print("personal mic sender stopped")

    def _global_mic_sender_loop(self) -> None:
        """Send USB Audio Device mic audio to remote device."""
        if self.global_mic_id is None:
            print("USB Audio Device mic not available")
            self.global_mic_sending = False
            return
        
        print(f"Starting USB Audio Device mic sender to {self.remote_ip}:{AUDIO_PORT_GLOBAL_MIC}")
        
        sock = self._create_udp_socket()
        stream = None
        
        try:
            # Get device info to check actual channel count
            device_info = self.p.get_device_info_by_index(self.global_mic_id)
            input_channels = int(device_info.get('maxInputChannels', 1))
            
            print(f"Global mic has {input_channels} input channels")
            
            # Create PyAudio stream for USB Audio Device mic with correct channel count
            stream = self.p.open(
                format=FORMAT,
                channels=input_channels,  # Use actual channel count from device
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
                input_device_index=self.global_mic_id
            )
            
            seq_num = 0
            
            while self.running and self.global_mic_sending:
                try:
                    # Read audio data
                    data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                    
                    # If input is mono but we need stereo for output, convert mono to stereo
                    if input_channels == 1 and CHANNELS == 2:
                        # Convert mono to stereo by duplicating each sample
                        mono_data = np.frombuffer(data, dtype=np.int16)
                        stereo_data = np.repeat(mono_data, 2)
                        data = stereo_data.tobytes()
                    
                    # Create packet with sequence number
                    packet = struct.pack(">I", seq_num) + data
                    
                    # Send packet
                    sock.sendto(packet, (self.remote_ip, AUDIO_PORT_GLOBAL_MIC))
                    
                    seq_num += 1
                    
                except Exception as e:
                    if self.running and self.global_mic_sending:
                        print(f"Error in USB Audio Device mic sender: {e}")
                        time.sleep(1.0)
        finally:
            if stream:
                stream.stop_stream()
                stream.close()
            sock.close()
            print("USB Audio Device mic sender stopped")
    
    def _personal_mic_receiver_loop(self) -> None:
        """Receive personal mic audio from remote device."""
        print(f"Starting personal mic receiver on port {AUDIO_PORT_PERSONAL_MIC}")
        sock = self._create_receiver_socket(AUDIO_PORT_PERSONAL_MIC)
        
        buffer = {}  # Store packets by sequence number for reordering
        next_seq = 0  # Next expected sequence number
        buffer_size = 10  # Max packets to buffer for reordering
        
        try:
            while self.running:
                try:
                    # Receive packet with sequence number
                    data, addr = sock.recvfrom(65536)
                    
                    if len(data) < 4:  # Need at least 4 bytes for sequence number
                        continue
                    
                    # First 4 bytes contain the sequence number
                    seq_num = struct.unpack(">I", data[:4])[0]
                    
                    # Rest of the packet contains the audio data
                    audio_data = data[4:]
                    
                    # Add to buffer
                    buffer[seq_num] = audio_data
                    
                    # Process packets in order
                    while next_seq in buffer:
                        # Get the next packet
                        audio_data = buffer.pop(next_seq)
                        
                        # Call callback if registered
                        if self.on_personal_mic_received:
                            self.on_personal_mic_received(audio_data)
                        
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
                        print(f"Error in personal mic receiver: {e}")
                        time.sleep(1.0)
        finally:
            sock.close()
            print("personal mic receiver stopped")
    
    def _global_mic_receiver_loop(self) -> None:
        """Receive USB Audio Device mic audio from remote device."""
        print(f"Starting USB Audio Device mic receiver on port {AUDIO_PORT_GLOBAL_MIC}")
        sock = self._create_receiver_socket(AUDIO_PORT_GLOBAL_MIC)
        
        buffer = {}  # Store packets by sequence number for reordering
        next_seq = 0  # Next expected sequence number
        buffer_size = 10  # Max packets to buffer for reordering
        
        try:
            while self.running:
                try:
                    # Receive packet with sequence number
                    data, addr = sock.recvfrom(65536)
                    
                    if len(data) < 4:  # Need at least 4 bytes for sequence number
                        continue
                    
                    # First 4 bytes contain the sequence number
                    seq_num = struct.unpack(">I", data[:4])[0]
                    
                    # Rest of the packet contains the audio data
                    audio_data = data[4:]
                    
                    # Add to buffer
                    buffer[seq_num] = audio_data
                    
                    # Process packets in order
                    while next_seq in buffer:
                        # Get the next packet
                        audio_data = buffer.pop(next_seq)
                        
                        # Call callback if registered
                        if self.on_global_mic_received:
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
                        print(f"Error in USB Audio Device mic receiver: {e}")
                        time.sleep(1.0)
        finally:
            sock.close()
            print("USB Audio Device mic receiver stopped")


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
        print(f"Received USB Audio Device mic audio: {len(data)} bytes")
    
    audio_streamer.register_personal_mic_callback(on_personal_mic_audio)
    audio_streamer.register_global_mic_callback(on_global_mic_audio)
    
    audio_streamer.start()
    
    try:
        print("\nTesting different pressure states:")
        print("\n1. Remote pressure, local no pressure")
        time.sleep(5)
        
        print("\n2. Both have pressure")
        system_state.update_local_state({"pressure": True})
        time.sleep(5)
        
        print("\n3. Local pressure, remote no pressure")
        system_state.update_remote_state({"pressure": False})
        time.sleep(5)
        
        print("\n4. Neither has pressure")
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
