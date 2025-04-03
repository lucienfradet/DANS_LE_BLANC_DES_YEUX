"""
Simplified audio system for the Dans le Blanc des Yeux installation.
This version assumes audio devices are stable and focuses on core functionality.

STATE LOGIC:
1. When both have pressure:
   - Play with LEFT channel muted
   - Send personal mic (TX) to remote

2. When remote has pressure and local doesn't:
   - Play with RIGHT channel muted
   - Send global mic (USB) to remote

3. When local has pressure and remote doesn't:
   - Play with LEFT channel muted
   - Send personal mic (TX) to remote

4. When neither has pressure: No audio
"""

import threading
import socket
import struct
import numpy as np
import pyaudio
import configparser
import time
from typing import Dict, Callable

from system_state import system_state

# Audio configuration
CHUNK_SIZE = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 2
RATE = 44100

# Network configuration
AUDIO_PORT = 6000  # Single port for audio streaming

class AudioSystem:
    """
    Simplified audio system that handles both streaming and playback.
    """
    
    def __init__(self, remote_ip: str):
        self.remote_ip = remote_ip
        
        # Device names (can be loaded from config)
        self.personal_mic_name = "TX 96Khz"
        self.global_mic_name = "USB Audio Device"
        
        # Configure from config file if available
        self._load_config()
        
        # PyAudio instance
        self.p = pyaudio.PyAudio()
        
        # Device IDs (will be set during initialization)
        self.personal_mic_id = None
        self.global_mic_id = None
        self.speaker_id = None
        
        # Input and output streams
        self.personal_mic_stream = None
        self.global_mic_stream = None
        self.speaker_stream = None
        
        # Current state
        self.current_mic_sending = None  # "personal", "global", or None
        self.current_muting = None  # "left", "right", or None
        
        # Threading
        self.running = False
        self.threads = []
        self.lock = threading.Lock()
        
        # Network sockets
        self.send_socket = None
        self.receive_socket = None
        
        # Audio data buffer for playback
        self.audio_buffer = []
        self.buffer_lock = threading.Lock()
        
        # Find and initialize audio devices once
        self._find_audio_devices()
        
        # Register as observer for state changes
        system_state.add_observer(self._on_state_change)
        
        print(f"Audio system initialized with remote IP: {remote_ip}")
    
    def _load_config(self):
        """Load settings from config.ini"""
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
        except Exception as e:
            print(f"Error loading audio config: {e}")
            print("Using default audio device names")
    
    def _find_audio_devices(self):
        """Find audio devices by name - done once at initialization."""
        print("\nIdentifying audio devices...")
        
        # Print all audio devices for debugging
        for i in range(self.p.get_device_count()):
            dev_info = self.p.get_device_info_by_index(i)
            print(f"Device {i}: {dev_info['name']}")
            print(f"  Input channels: {dev_info['maxInputChannels']}")
            print(f"  Output channels: {dev_info['maxOutputChannels']}")
            print(f"  Default sample rate: {dev_info['defaultSampleRate']}")
            
            # Check for devices
            if self.personal_mic_name in dev_info["name"] and dev_info["maxInputChannels"] > 0:
                self.personal_mic_id = i
                print(f"Found {self.personal_mic_name} mic: Device {i}")
            
            if self.global_mic_name in dev_info["name"] and dev_info["maxInputChannels"] > 0:
                self.global_mic_id = i
                print(f"Found {self.global_mic_name} mic: Device {i}")
            
            # For speaker, first try to find the personal device's output
            if self.personal_mic_name in dev_info["name"] and dev_info["maxOutputChannels"] > 0:
                self.speaker_id = i
                print(f"Found {self.personal_mic_name} speaker: Device {i}")
        
        # If no global mic found, try fallback
        if self.global_mic_id is None:
            for i in range(self.p.get_device_count()):
                dev_info = self.p.get_device_info_by_index(i)
                if ("USB Audio" in dev_info["name"] and 
                    self.personal_mic_name not in dev_info["name"] and 
                    dev_info["maxInputChannels"] > 0):
                    self.global_mic_id = i
                    print(f"Found fallback USB mic: Device {i}")
                    break
        
        # If no speaker found, use default
        if self.speaker_id is None:
            try:
                default_info = self.p.get_default_output_device_info()
                self.speaker_id = default_info["index"]
                print(f"Using default output device: {default_info['name']} (Device {self.speaker_id})")
            except Exception as e:
                print(f"Warning: Could not find audio output device: {e}")
        
        # Report found devices
        print(f"\nSelected devices:")
        print(f"  Personal mic (TX): {self.personal_mic_id}")
        print(f"  Global mic (USB): {self.global_mic_id}")
        print(f"  Speaker: {self.speaker_id}")
        
        # Check if we have the required devices
        if self.personal_mic_id is None:
            print(f"WARNING: {self.personal_mic_name} mic not found!")
        if self.global_mic_id is None:
            print(f"WARNING: {self.global_mic_name} mic not found!")
        if self.speaker_id is None:
            print(f"WARNING: No audio output device found!")
    
    def start(self):
        """Start the audio system with all streams."""
        print("Starting audio system...")
        self.running = True
        
        # Create network sockets
        self.send_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.send_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
        
        self.receive_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.receive_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.receive_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        self.receive_socket.bind(("0.0.0.0", AUDIO_PORT))
        self.receive_socket.settimeout(0.5)  # Set a timeout for responsive shutdown
        
        # Start audio input for personal mic if available
        if self.personal_mic_id is not None:
            try:
                def personal_mic_callback(in_data, frame_count, time_info, status):
                    if self.running and self.current_mic_sending == "personal":
                        self._send_audio(in_data, mic_type="personal")
                    return (None, pyaudio.paContinue)
                
                self.personal_mic_stream = self.p.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    frames_per_buffer=CHUNK_SIZE,
                    input_device_index=self.personal_mic_id,
                    stream_callback=personal_mic_callback
                )
                self.personal_mic_stream.start_stream()
                print(f"Started personal mic stream from device {self.personal_mic_id}")
            except Exception as e:
                print(f"Error starting personal mic stream: {e}")
        
        # Start audio input for global mic if available
        if self.global_mic_id is not None:
            try:
                def global_mic_callback(in_data, frame_count, time_info, status):
                    if self.running and self.current_mic_sending == "global":
                        self._send_audio(in_data, mic_type="global")
                    return (None, pyaudio.paContinue)
                
                self.global_mic_stream = self.p.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    frames_per_buffer=CHUNK_SIZE,
                    input_device_index=self.global_mic_id,
                    stream_callback=global_mic_callback
                )
                self.global_mic_stream.start_stream()
                print(f"Started global mic stream from device {self.global_mic_id}")
            except Exception as e:
                print(f"Error starting global mic stream: {e}")
        
        # Start audio output if available
        if self.speaker_id is not None:
            try:
                def speaker_callback(in_data, frame_count, time_info, status):
                    return (self._get_output_audio(frame_count, self.current_muting), pyaudio.paContinue)
                
                self.speaker_stream = self.p.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    output=True,
                    frames_per_buffer=CHUNK_SIZE,
                    output_device_index=self.speaker_id,
                    stream_callback=speaker_callback
                )
                self.speaker_stream.start_stream()
                print(f"Started speaker stream to device {self.speaker_id}")
            except Exception as e:
                print(f"Error starting speaker stream: {e}")
        
        # Start receiver thread
        receiver_thread = threading.Thread(target=self._receiver_loop)
        receiver_thread.daemon = True
        receiver_thread.start()
        self.threads.append(receiver_thread)
        
        # Set initial state
        self._update_state_based_on_system()
        
        print("Audio system started")
        return True
    
    def stop(self):
        """Stop the audio system and clean up resources."""
        print("Stopping audio system...")
        self.running = False
        
        # Wait for threads to finish
        for thread in self.threads:
            thread.join(timeout=1.0)
        
        # Close audio streams
        if self.personal_mic_stream:
            self.personal_mic_stream.stop_stream()
            self.personal_mic_stream.close()
            self.personal_mic_stream = None
        
        if self.global_mic_stream:
            self.global_mic_stream.stop_stream()
            self.global_mic_stream.close()
            self.global_mic_stream = None
        
        if self.speaker_stream:
            self.speaker_stream.stop_stream()
            self.speaker_stream.close()
            self.speaker_stream = None
        
        # Close sockets
        if self.send_socket:
            self.send_socket.close()
            self.send_socket = None
        
        if self.receive_socket:
            self.receive_socket.close()
            self.receive_socket = None
        
        # Clean up PyAudio
        if self.p:
            self.p.terminate()
            self.p = None
        
        print("Audio system stopped")
    
    def _on_state_change(self, changed_state: str):
        """Handle system state changes."""
        if changed_state in ["local", "remote"]:
            self._update_state_based_on_system()
    
    def _update_state_based_on_system(self):
        """Update streaming and muting based on current system state."""
        local_state = system_state.get_local_state()
        remote_state = system_state.get_remote_state()
        
        old_mic = self.current_mic_sending
        old_muting = self.current_muting
        
        # Only proceed if remote is connected
        if not remote_state.get("connected", False):
            self.current_mic_sending = None
            self.current_muting = None
        
        # Case 1: Both have pressure
        elif local_state.get("pressure", False) and remote_state.get("pressure", False):
            self.current_mic_sending = "personal"  # Send personal mic (TX)
            self.current_muting = "left"           # Mute LEFT channel
        
        # Case 2: Remote has pressure but local doesn't
        elif remote_state.get("pressure", False) and not local_state.get("pressure", False):
            self.current_mic_sending = "global"    # Send global mic (USB)
            self.current_muting = "right"          # Mute RIGHT channel
        
        # Case 3: Local has pressure but remote doesn't
        elif local_state.get("pressure", False) and not remote_state.get("pressure", False):
            self.current_mic_sending = "personal"  # Send personal mic (TX)
            self.current_muting = "left"           # Mute LEFT channel
        
        # Case 4: No pressure on either side
        else:
            self.current_mic_sending = None
            self.current_muting = None
        
        if old_mic != self.current_mic_sending or old_muting != self.current_muting:
            print(f"Audio state changed:")
            print(f"  Streaming: {old_mic} -> {self.current_mic_sending}")
            print(f"  Muting: {old_muting} -> {self.current_muting}")
            
            # Clear audio buffer when state changes
            with self.buffer_lock:
                self.audio_buffer = []
    
    def _send_audio(self, audio_data, mic_type):
        """Send audio data to the remote device."""
        if not self.running or not self.send_socket:
            return
        
        try:
            # Add mic type identifier (0 for personal, 1 for global)
            mic_id_byte = b'\x00' if mic_type == "personal" else b'\x01'
            
            # Create packet with sequence number and mic type
            # Use a simple counter for the sequence number
            seq_num = int(time.time() * 1000) & 0xFFFFFFFF  # Use timestamp as seq num
            packet = struct.pack(">I", seq_num) + mic_id_byte + audio_data
            
            # Send packet
            self.send_socket.sendto(packet, (self.remote_ip, AUDIO_PORT))
        except Exception as e:
            print(f"Error sending audio: {e}")
    
    def _get_output_audio(self, frame_count, muting):
        """
        Get audio data for output with appropriate channel muting.
        If no data available, returns silence.
        """
        # Default to silence if no data or no playback
        output = np.zeros(frame_count * CHANNELS, dtype=np.int16)
        
        if muting is None:
            # No playback, return silence
            return output.tobytes()
        
        # Get audio data from buffer
        with self.buffer_lock:
            if not self.audio_buffer:
                return output.tobytes()
            
            # Get the oldest chunk of data
            data = self.audio_buffer.pop(0)
        
        # Apply channel muting
        try:
            # Convert bytes to numpy array
            audio_array = np.frombuffer(data, dtype=np.int16)
            
            # Ensure array is the right size
            if len(audio_array) < frame_count * CHANNELS:
                # Pad with zeros if too small
                padding = np.zeros(frame_count * CHANNELS - len(audio_array), dtype=np.int16)
                audio_array = np.concatenate([audio_array, padding])
            elif len(audio_array) > frame_count * CHANNELS:
                # Truncate if too large
                audio_array = audio_array[:frame_count * CHANNELS]
            
            # Reshape to separate channels
            audio_array = audio_array.reshape(-1, 2)
            
            # Apply muting
            if muting == "left":
                audio_array[:, 0] = 0  # Mute left channel
            elif muting == "right":
                audio_array[:, 1] = 0  # Mute right channel
            
            # Return as bytes
            return audio_array.tobytes()
        except Exception as e:
            print(f"Error processing audio output: {e}")
            return output.tobytes()
    
    def _receiver_loop(self):
        """Receive audio from remote device."""
        print(f"Starting audio receiver on port {AUDIO_PORT}")
        
        buffer = {}  # Store packets by sequence number for reordering
        next_seq = 0  # Next expected sequence number
        buffer_size = 10  # Max packets to buffer for reordering
        
        try:
            while self.running:
                try:
                    # Receive packet
                    data, addr = self.receive_socket.recvfrom(65536)
                    
                    if len(data) < 5:  # Need at least 4 bytes for sequence number + 1 for mic ID
                        continue
                    
                    # First 4 bytes contain the sequence number
                    seq_num = struct.unpack(">I", data[:4])[0]
                    
                    # Next byte indicates mic type (0=personal, 1=global)
                    mic_type_byte = data[4:5]
                    
                    # Rest of the packet contains the audio data
                    audio_data = data[5:]
                    
                    # Add to buffer
                    buffer[seq_num] = audio_data
                    
                    # Process packets in order
                    while next_seq in buffer:
                        # Get the next packet
                        audio_data = buffer.pop(next_seq)
                        
                        # Add to playback buffer if we're in playback mode
                        if self.current_muting is not None:
                            with self.buffer_lock:
                                # Limit buffer size
                                if len(self.audio_buffer) < 5:  # Keep buffer small to reduce latency
                                    self.audio_buffer.append(audio_data)
                        
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
            print("Audio receiver stopped")


# Test function
def test_audio_system():
    """Test the audio system."""
    
    # Set up system state for testing
    system_state.update_local_state({"pressure": False})
    system_state.update_remote_state({"pressure": True, "connected": True})
    
    # Initialize audio system
    audio_system = AudioSystem("127.0.0.1")
    audio_system.start()
    
    try:
        print("\nTesting different pressure states:")
        print("\n1. Remote pressure, local no pressure - RIGHT muted, send GLOBAL")
        time.sleep(5)
        
        print("\n2. Both have pressure - LEFT muted, send PERSONAL")
        system_state.update_local_state({"pressure": True})
        time.sleep(5)
        
        print("\n3. Local pressure, remote no pressure - LEFT muted, send PERSONAL")
        system_state.update_remote_state({"pressure": False})
        time.sleep(5)
        
        print("\n4. Neither has pressure - No audio")
        system_state.update_local_state({"pressure": False})
        time.sleep(5)
        
        print("\nAudio system test complete")
        
    except KeyboardInterrupt:
        print("Test interrupted by user")
    finally:
        audio_system.stop()


# Run test if executed directly
if __name__ == "__main__":
    test_audio_system()
