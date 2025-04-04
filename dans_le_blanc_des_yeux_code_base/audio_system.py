"""
Audio system for the Dans le Blanc des Yeux installation.
Handles audio streaming between devices based on pressure states.

Streaming Logic:
1. When both have pressure: Both devices send and receive/play audio
2. When remote has pressure, local doesn't: Local device sends mic audio but plays nothing
3. When local has pressure, remote doesn't: Local device plays received audio but sends nothing
4. When neither has pressure: No playback or sending

Usage:
    from audio_system import AudioSystem
    audio_system = AudioSystem(remote_ip)
    audio_system.start()
"""

import threading
import time
import socket
import struct
import pyaudio
import configparser
import numpy as np
from typing import Dict, Any, Optional, List, Tuple

from system_state import system_state

# Audio stream parameters
CHUNK_SIZE = 1024            # Audio buffer chunk size
FORMAT = pyaudio.paInt16     # Audio format (16-bit PCM)
CHANNELS = 1                 # Mono audio for network efficiency
RATE = 48000                 # Sample rate (samples per second)
UDP_PORT = 5002              # UDP port for audio streaming
BUFFER_SIZE = 65536          # UDP receive buffer size
JITTER_BUFFER_SIZE = 3       # Number of chunks in jitter buffer

class JitterBuffer:
    """Simple jitter buffer to handle network packet arrival timing issues."""
    
    def __init__(self, max_size: int = JITTER_BUFFER_SIZE):
        self.buffer = []
        self.max_size = max_size
        self.sequence = 0
        self.lock = threading.Lock()
    
    def add(self, data: bytes, seq: int) -> None:
        """Add a packet to the jitter buffer."""
        with self.lock:
            # Check if buffer is already full
            if len(self.buffer) >= self.max_size:
                # Buffer is full, don't add more data
                return
            
            # Add data with sequence number
            self.buffer.append((seq, data))
            
            # Sort buffer by sequence number
            self.buffer.sort(key=lambda x: x[0])
    
    def get(self) -> Optional[bytes]:
        """Get the next packet from the jitter buffer."""
        with self.lock:
            if not self.buffer:
                return None
            
            # Get the oldest packet
            _, data = self.buffer.pop(0)
            return data
    
    def clear(self) -> None:
        """Clear the jitter buffer."""
        with self.lock:
            self.buffer = []
            self.sequence = 0
    
    def is_ready(self) -> bool:
        """Check if the buffer has enough data to start playback."""
        with self.lock:
            return len(self.buffer) >= min(2, self.max_size)

class AudioSystem:
    """Handles audio streaming between devices based on pressure states."""
    
    def __init__(self, remote_ip: str):
        self.remote_ip = remote_ip
        
        # Audio state
        self.sending_audio = False
        self.receiving_audio = False
        self.output_muted = True
        
        # PyAudio objects
        self.pyaudio = None
        self.input_stream = None
        self.output_stream = None
        
        # Mic and output device information
        self.input_device_index = None
        self.output_device_index = None
        self.mic_name = None
        
        # Audio format information
        self.input_rate = RATE
        self.output_rate = RATE
        self.input_channels = CHANNELS
        self.output_channels = CHANNELS
        
        # Threading
        self.running = False
        self.threads = []
        self.lock = threading.Lock()
        
        # Network
        self.send_socket = None
        self.receive_socket = None
        self.send_sequence = 0
        self.jitter_buffer = JitterBuffer()
        
        # Statistics
        self.packets_sent = 0
        self.packets_received = 0
        self.last_stats_time = time.time()
        
        # Load configuration
        self._load_config()
        
        # Register as observer for state changes
        system_state.add_observer(self._on_state_change)
        
        print(f"Audio system initialized with remote IP: {remote_ip}")
    
    def _load_config(self) -> None:
        """Load audio settings from config.ini"""
        try:
            config = configparser.ConfigParser()
            config.read('config.ini')
            
            if 'audio' in config:
                self.mic_name = config['audio'].get('global_mic_name', 'USB Audio Device')
                print(f"Using mic name from config: {self.mic_name}")
                
                # Load audio format settings if provided
                self.input_rate = config['audio'].getint('input_rate', RATE)
                self.output_rate = config['audio'].getint('output_rate', RATE)
                self.input_channels = config['audio'].getint('input_channels', CHANNELS)
                self.output_channels = config['audio'].getint('output_channels', CHANNELS)
            else:
                self.mic_name = 'USB Audio Device'
                print(f"No audio config found, using default mic name: {self.mic_name}")
        except Exception as e:
            print(f"Error loading audio config: {e}")
            self.mic_name = 'USB Audio Device'
            print(f"Using default mic name: {self.mic_name}")
    
    def start(self) -> bool:
        """Start the audio system."""
        print("Starting audio system...")
        self.running = True
        
        # Initialize PyAudio
        if not self._init_pyaudio():
            print("Failed to initialize PyAudio")
            return False
        
        # Initialize network sockets
        if not self._init_network():
            print("Failed to initialize network sockets")
            return False
        
        # Start audio streams
        self._start_audio_streams()
        
        # Start audio receiving thread
        receiver_thread = threading.Thread(target=self._audio_receiver_loop)
        receiver_thread.daemon = True
        receiver_thread.start()
        self.threads.append(receiver_thread)
        
        # Start audio sending thread
        sender_thread = threading.Thread(target=self._audio_sender_loop)
        sender_thread.daemon = True
        sender_thread.start()
        self.threads.append(sender_thread)
        
        # Start audio playback thread
        playback_thread = threading.Thread(target=self._audio_playback_loop)
        playback_thread.daemon = True
        playback_thread.start()
        self.threads.append(playback_thread)
        
        # Start stats reporting thread
        stats_thread = threading.Thread(target=self._stats_reporting_loop)
        stats_thread.daemon = True
        stats_thread.start()
        self.threads.append(stats_thread)
        
        # Check initial state to set up audio routing
        self._update_audio_based_on_state()
        
        print("Audio system started")
        return True
    
    def _init_network(self) -> bool:
        """Initialize network sockets for audio streaming."""
        try:
            # Create socket for sending audio
            self.send_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            
            # Create socket for receiving audio
            self.receive_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.receive_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.receive_socket.bind(("0.0.0.0", UDP_PORT))
            self.receive_socket.settimeout(0.5)  # Set timeout for responsive shutdown
            
            print(f"Network initialized: sending to {self.remote_ip}:{UDP_PORT}, receiving on port {UDP_PORT}")
            return True
        except Exception as e:
            print(f"Error initializing network: {e}")
            return False
    
    def _audio_sender_loop(self) -> None:
        """Thread function to capture and send audio from microphone."""
        print("Audio sender thread started")
        
        # Track sequence number for packet ordering
        sequence = 0
        
        try:
            while self.running:
                # Only send if we're supposed to
                if self.sending_audio and self.input_stream:
                    try:
                        # Read from microphone
                        audio_data = self.input_stream.read(CHUNK_SIZE, exception_on_overflow=False)
                        
                        # Add sequence number to packet
                        packet = struct.pack(">I", sequence) + audio_data
                        
                        # Send to remote device
                        self.send_socket.sendto(packet, (self.remote_ip, UDP_PORT))
                        
                        # Update sequence number
                        sequence = (sequence + 1) % 10000
                        
                        # Update stats
                        self.packets_sent += 1
                    except Exception as e:
                        print(f"Error sending audio: {e}")
                        time.sleep(0.1)
                else:
                    # Not sending, sleep to reduce CPU usage
                    time.sleep(0.1)
        except Exception as e:
            if self.running:
                print(f"Audio sender thread error: {e}")
        finally:
            print("Audio sender thread stopped")
    
    def _audio_receiver_loop(self) -> None:
        """Thread function to receive audio packets from network."""
        print("Audio receiver thread started")
        
        try:
            while self.running:
                try:
                    # Receive audio packet
                    data, addr = self.receive_socket.recvfrom(BUFFER_SIZE)
                    
                    # Only process if we're supposed to be receiving
                    if self.receiving_audio and len(data) > 4:  # Ensure we have at least a sequence number
                        # Extract sequence number from packet
                        sequence = struct.unpack(">I", data[:4])[0]
                        
                        # Extract audio data
                        audio_data = data[4:]
                        
                        # Add to jitter buffer
                        self.jitter_buffer.add(audio_data, sequence)
                        
                        # Update stats
                        self.packets_received += 1
                except socket.timeout:
                    # This is expected due to the socket timeout
                    pass
                except Exception as e:
                    if self.running:
                        print(f"Error receiving audio: {e}")
                        time.sleep(0.1)
        except Exception as e:
            if self.running:
                print(f"Audio receiver thread error: {e}")
        finally:
            print("Audio receiver thread stopped")
    
    def _audio_playback_loop(self) -> None:
        """Thread function to play received audio."""
        print("Audio playback thread started")
        
        # Create silent buffer for when no audio is available
        silent_buffer = b'\x00' * (CHUNK_SIZE * self.output_channels * 2)  # 2 bytes per sample for 16-bit
        
        try:
            while self.running:
                if not self.output_muted and self.output_stream:
                    # Check if we have audio to play
                    if self.receiving_audio and self.jitter_buffer.is_ready():
                        # Get audio from jitter buffer
                        audio_data = self.jitter_buffer.get()
                        
                        if audio_data:
                            # Play audio
                            self.output_stream.write(audio_data)
                        else:
                            # No audio available in buffer, play silence
                            self.output_stream.write(silent_buffer)
                    else:
                        # Not receiving or buffer not ready, play silence
                        self.output_stream.write(silent_buffer)
                else:
                    # Output is muted, just sleep
                    time.sleep(0.01)
        except Exception as e:
            if self.running:
                print(f"Audio playback thread error: {e}")
        finally:
            print("Audio playback thread stopped")
    
    def _stats_reporting_loop(self) -> None:
        """Thread function to periodically report audio statistics."""
        print("Stats reporting thread started")
        
        try:
            while self.running:
                # Sleep for 10 seconds
                for _ in range(100):
                    if not self.running:
                        break
                    time.sleep(0.1)
                
                if not self.running:
                    break
                
                # Calculate rates
                now = time.time()
                elapsed = now - self.last_stats_time
                
                if elapsed > 0:
                    send_rate = self.packets_sent / elapsed
                    recv_rate = self.packets_received / elapsed
                    
                    # Only report if we're sending or receiving
                    if self.sending_audio or self.receiving_audio:
                        print(f"Audio stats: Sending {send_rate:.1f} packets/s, Receiving {recv_rate:.1f} packets/s")
                        print(f"Jitter buffer size: {len(self.jitter_buffer.buffer)} packets")
                    
                    # Reset counters
                    self.packets_sent = 0
                    self.packets_received = 0
                    self.last_stats_time = now
        except Exception as e:
            if self.running:
                print(f"Stats reporting thread error: {e}")
        finally:
            print("Stats reporting thread stopped")
    
    def _start_audio_streams(self) -> None:
        """Initialize and start audio input and output streams."""
        try:
            # Start input stream (microphone)
            self.input_stream = self.pyaudio.open(
                rate=self.input_rate,
                channels=self.input_channels,
                format=FORMAT,
                input=True,
                input_device_index=self.input_device_index,
                frames_per_buffer=CHUNK_SIZE,
                stream_callback=None  # No callback, we'll read directly
            )
            
            # Start output stream
            self.output_stream = self.pyaudio.open(
                rate=self.output_rate,
                channels=self.output_channels,
                format=FORMAT,
                output=True,
                output_device_index=self.output_device_index,
                frames_per_buffer=CHUNK_SIZE,
                stream_callback=None  # No callback, we'll write directly
            )
            
            print("Audio streams started")
        except Exception as e:
            print(f"Error starting audio streams: {e}")
            if self.input_stream:
                self.input_stream.close()
            if self.output_stream:
                self.output_stream.close()
    
    def _stop_audio_streams(self) -> None:
        """Stop and close audio streams."""
        if self.input_stream:
            try:
                self.input_stream.stop_stream()
                self.input_stream.close()
            except Exception as e:
                print(f"Error closing input stream: {e}")
            self.input_stream = None
        
        if self.output_stream:
            try:
                self.output_stream.stop_stream()
                self.output_stream.close()
            except Exception as e:
                print(f"Error closing output stream: {e}")
            self.output_stream = None
    
    def _on_state_change(self, changed_state: str) -> None:
        """Handle system state changes."""
        if changed_state in ["local", "remote"]:
            self._update_audio_based_on_state()
    
    def _update_audio_based_on_state(self) -> None:
        """Update audio streaming based on pressure states."""
        local_state = system_state.get_local_state()
        remote_state = system_state.get_remote_state()
        
        # Only proceed if remote is connected
        if not remote_state.get("connected", False):
            self.sending_audio = False
            self.receiving_audio = False
            self.output_muted = True
            print("Remote not connected, audio disabled")
            return
        
        # Get pressure states
        local_pressure = local_state.get("pressure", False)
        remote_pressure = remote_state.get("pressure", False)
        
        # Case 1: Both have pressure - both send and receive
        if local_pressure and remote_pressure:
            self.sending_audio = True
            self.receiving_audio = True
            self.output_muted = False
            print("Both have pressure: sending and receiving audio")
        
        # Case 2: Remote has pressure, local doesn't - send only
        elif remote_pressure and not local_pressure:
            self.sending_audio = True
            self.receiving_audio = False
            self.output_muted = True
            print("Remote has pressure: sending audio but not playing")
        
        # Case 3: Local has pressure, remote doesn't - receive only
        elif local_pressure and not remote_pressure:
            self.sending_audio = False
            self.receiving_audio = True
            self.output_muted = False
            print("Local has pressure: receiving audio but not sending")
        
        # Case 4: Neither has pressure - no audio
        else:
            self.sending_audio = False
            self.receiving_audio = False
            self.output_muted = True
            print("No pressure: audio disabled")
        
        # Update system state
        audio_state = {
            "playing": not self.output_muted,
            "streaming_mic": self.mic_name if self.sending_audio else "none",
            "muted_channels": []
        }
        system_state.update_audio_state(audio_state)
        
        # Reset jitter buffer when changing state
        self.jitter_buffer.clear()
    
    def stop(self) -> None:
        """Stop the audio system and release resources."""
        print("Stopping audio system...")
        self.running = False
        
        # Stop audio streams
        self._stop_audio_streams()
        
        # Close network sockets
        if self.send_socket:
            self.send_socket.close()
        if self.receive_socket:
            self.receive_socket.close()
        
        # Wait for threads to finish
        for thread in self.threads:
            thread.join(timeout=1.0)
        
        # Terminate PyAudio
        if self.pyaudio:
            self.pyaudio.terminate()
        
        print("Audio system stopped")
    
    def _print_audio_devices(self) -> None:
        """Print available audio devices for debugging."""
        print("\n=== Available Audio Devices ===")
        for i in range(self.pyaudio.get_device_count()):
            device_info = self.pyaudio.get_device_info_by_index(i)
            name = device_info.get('name', 'Unknown')
            in_channels = device_info.get('maxInputChannels', 0)
            out_channels = device_info.get('maxOutputChannels', 0)
            print(f"Device {i}: {name}")
            print(f"  Input channels: {in_channels}, Output channels: {out_channels}")
            print(f"  Default sample rate: {device_info.get('defaultSampleRate', 'Unknown')}")
        print("=== End Device List ===\n")
    
    def _find_device_by_name(self, name: str, is_input: bool = True) -> Optional[int]:
        """Find an audio device by name and return its index."""
        for i in range(self.pyaudio.get_device_count()):
            device_info = self.pyaudio.get_device_info_by_index(i)
            device_name = device_info.get('name', '')
            
            # Check if device name contains the search name (case insensitive)
            if name.lower() in device_name.lower():
                # Check if it's an input or output device as requested
                if (is_input and device_info.get('maxInputChannels', 0) > 0) or \
                   (not is_input and device_info.get('maxOutputChannels', 0) > 0):
                    return i
        return None
    
    def _find_any_input_device(self) -> Optional[int]:
        """Find any available input device."""
        for i in range(self.pyaudio.get_device_count()):
            device_info = self.pyaudio.get_device_info_by_index(i)
            if device_info.get('maxInputChannels', 0) > 0:
                return i
        return None
    
    def _find_any_output_device(self) -> Optional[int]:
        """Find any available output device."""
        for i in range(self.pyaudio.get_device_count()):
            device_info = self.pyaudio.get_device_info_by_index(i)
            if device_info.get('maxOutputChannels', 0) > 0:
                return i
        return None
    
    def _init_pyaudio(self) -> bool:
        """Initialize PyAudio and find audio devices."""
        try:
            self.pyaudio = pyaudio.PyAudio()
            
            # Find input device (microphone)
            self.input_device_index = self._find_device_by_name(self.mic_name, is_input=True)
            if self.input_device_index is None:
                print(f"Warning: Could not find input device '{self.mic_name}'")
                # Find any input device
                self.input_device_index = self._find_any_input_device()
                if self.input_device_index is None:
                    print("Error: No input device found")
                    return False
                print(f"Using alternate input device (index {self.input_device_index})")
            else:
                print(f"Found input device '{self.mic_name}' (index {self.input_device_index})")
                
                # Get device info to check actual input rate and channels
                device_info = self.pyaudio.get_device_info_by_index(self.input_device_index)
                self.input_rate = int(device_info.get('defaultSampleRate', RATE))
                self.input_channels = min(int(device_info.get('maxInputChannels', CHANNELS)), CHANNELS)
                print(f"Input device rate: {self.input_rate}, channels: {self.input_channels}")
            
            # Find output device (TX 96Khz)
            self.output_device_index = self._find_any_output_device()
            if self.output_device_index is None:
                print("Error: No output device found")
                return False
            
            # Get device info for output device
            device_info = self.pyaudio.get_device_info_by_index(self.output_device_index)
            self.output_rate = int(device_info.get('defaultSampleRate', RATE))
            self.output_channels = min(int(device_info.get('maxOutputChannels', CHANNELS)), CHANNELS)
            print(f"Output device (index {self.output_device_index})")
            print(f"Output device rate: {self.output_rate}, channels: {self.output_channels}")
            
            # Print available audio devices for debugging
            self._print_audio_devices()
            
            return True
        except Exception as e:
            print(f"Error initializing PyAudio: {e}")
            return False
