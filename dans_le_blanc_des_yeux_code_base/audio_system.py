"""
Audio system for the Dans le Blanc des Yeux installation.
Handles audio streaming between devices based on pressure states.

Streaming Logic:
1. When both have pressure: Both devices send and receive/play audio
2. When remote has pressure, local doesn't: Local device sends mic audio but plays nothing
3. When local has pressure, remote doesn't: Local device plays received audio but sends nothing
4. When neither has pressure: No playback or sending
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
            print(f
