"""
Audio streaming module for the Dans le Blanc des Yeux installation.
Handles bidirectional audio streaming with selective channel muting based on pressure state.

Audio Routing Logic:
1. When both have pressure:
   - Play with LEFT channel muted
   - Send personal mic (TX) to remote
2. When remote has pressure and local doesn't:
   - Play with RIGHT channel muted
   - Send global mic (USB) to remote
3. When local has pressure and remote doesn't:
   - Play with LEFT channel muted
   - Send personal mic (TX) to remote
4. When neither has pressure: No playback (mute both channels)
"""

import os
import time
import threading
import socket
import struct
import numpy as np
import pyaudio
import configparser
from typing import Dict, Optional, List, Tuple, Any

from system_state import system_state

# Port configuration
AUDIO_STREAM_PORT = 5002

class AudioSystem:
    """Handles audio streaming between devices with automatic channel muting."""
    
    def __init__(self, remote_ip: str):
        self.remote_ip = remote_ip
        self.running = False
        
        # Load configuration
        self._load_config()
        
        # PyAudio instance
        self.pa = None
        
        # Audio parameters
        self.channels = 2           # Stereo
        self.rate = 44100           # Sample rate (Hz)
        self.chunk_size = 1024      # Frames per buffer
        self.format = None          # Will be set during initialization
        self.buffer_size = 65536    # UDP buffer size
        
        # Audio device IDs
        self.output_device_id = None
        self.personal_mic_id = None
        self.global_mic_id = None
        
        # Active streams
        self.input_stream = None
        self.output_stream = None
        
        # Audio buffers
        self.output_buffer = np.zeros((self.chunk_size, self.channels), dtype=np.int16)
        self.buffer_lock = threading.Lock()
        
        # Network sockets
        self.sender_socket = None
        self.receiver_socket = None
        
        # Thread management
        self.threads = []
        self.stop_event = threading.Event()
        
        # State tracking
        self.is_sending = False
        self.current_mic = None  # 'personal' or 'global'
        self.muted_channel = 'both'  # 'left', 'right', or 'both'
        
        # Register as observer for state changes
        system_state.add_observer(self._on_state_change)
        
        print(f"Audio system initialized with remote IP: {remote_ip}")
    
    def _load_config(self) -> None:
        """Load audio configuration from config.ini"""
        config = configparser.ConfigParser()
        config.read('config.ini')
        
        # Load audio device settings with defaults
        if 'audio' in config:
            self.global_speaker_mute_channel = config['audio'].get('global_speaker_mute_channel', 'right')
            self.personal_speaker_mute_channel = config['audio'].get('personal_speaker_mute_channel', 'left')
            self.personal_mic_name = config['audio'].get('personal_mic_name', 'TX 96Khz')
            self.global_mic_name = config['audio'].get('global_mic_name', 'USB Audio Device')
        else:
            # Default settings
            self.global_speaker_mute_channel = 'right'
            self.personal_speaker_mute_channel = 'left'
            self.personal_mic_name = 'TX 96Khz'
            self.global_mic_name = 'USB Audio Device'
            
            # Create audio section in config if it doesn't exist
            if 'audio' not in config:
                config['audio'] = {}
                config['audio']['global_speaker_mute_channel'] = self.global_speaker_mute_channel
                config['audio']['personal_speaker_mute_channel'] = self.personal_speaker_mute_channel
                config['audio']['personal_mic_name'] = self.personal_mic_name
                config['audio']['global_mic_name'] = self.global_mic_name
                
                # Write to config file
                with open('config.ini', 'w') as configfile:
                    config.write(configfile)
                    
                print("Added audio settings to config.ini")
        
        print(f"Loaded audio configuration:")
        print(f"  Global speaker mute channel: {self.global_speaker_mute_channel}")
        print(f"  Personal speaker mute channel: {self.personal_speaker_mute_channel}")
        print(f"  Personal mic name: {self.personal_mic_name}")
        print(f"  Global mic name: {self.global_mic_name}")
    
    def start(self) -> bool:
        """Start the audio system."""
        print("Starting audio system...")
        self.running = True
        self.stop_event.clear()
        
        try:
            # Initialize PyAudio
            self.pa = pyaudio.PyAudio()
            
            # Set audio format
            self.format = pyaudio.paInt16
            
            # Find audio devices
            if not self._find_audio_devices():
                print("Failed to find required audio devices")
                self.stop()
                return False
            
            # Initialize network sockets
            if not self._init_network():
                print("Failed to initialize network")
                self.stop()
                return False
            
            # Start audio streams
            if not self._init_audio_streams():
                print("Failed to initialize audio streams")
                self.stop()
                return False
            
            # Start receiver thread
            receiver_thread = threading.Thread(target=self._audio_receiver_loop)
            receiver_thread.daemon = True
            receiver_thread.start()
            self.threads.append(receiver_thread)
            
            # Start sender thread
            sender_thread = threading.Thread(target=self._audio_sender_loop)
            sender_thread.daemon = True
            sender_thread.start()
            self.threads.append(sender_thread)
            
            # Update audio routing based on current state
            self._update_audio_routing()
            
            print("Audio system started successfully")
            return True
            
        except Exception as e:
            print(f"Error starting audio system: {e}")
            import traceback
            traceback.print_exc()
            self.stop()
            return False
    
    def stop(self) -> None:
        """Stop the audio system and release resources."""
        print("Stopping audio system...")
        self.running = False
        self.stop_event.set()
        
        # Stop and close audio streams
        if self.input_stream is not None:
            try:
                self.input_stream.stop_stream()
                self.input_stream.close()
            except Exception as e:
                print(f"Error closing input stream: {e}")
            self.input_stream = None
            
        if self.output_stream is not None:
            try:
                self.output_stream.stop_stream()
                self.output_stream.close()
            except Exception as e:
                print(f"Error closing output stream: {e}")
            self.output_stream = None
        
        # Terminate PyAudio
        if self.pa is not None:
            try:
                self.pa.terminate()
            except Exception as e:
                print(f"Error terminating PyAudio: {e}")
            self.pa = None
        
        # Close sockets
        if self.sender_socket is not None:
            try:
                self.sender_socket.close()
            except:
                pass
            self.sender_socket = None
            
        if self.receiver_socket is not None:
            try:
                self.receiver_socket.close()
            except:
                pass
            self.receiver_socket = None
        
        # Wait for threads to finish
        for thread in self.threads:
            thread.join(timeout=1.0)
        
        self.threads = []
            
        print("Audio system stopped")
    
    def _find_audio_devices(self) -> bool:
        """Find audio devices by name and set their IDs."""
        print("Scanning audio devices...")
        
        device_count = self.pa.get_device_count()
        output_devices = []
        personal_mic_candidates = []
        global_mic_candidates = []
        
        # Scan all devices
        for i in range(device_count):
            try:
                device_info = self.pa.get_device_info_by_index(i)
                device_name = device_info['name']
                
                print(f"  Device {i}: {device_name}")
                print(f"    Input Channels: {device_info['maxInputChannels']}")
                print(f"    Output Channels: {device_info['maxOutputChannels']}")
                
                # Find output device (any device with output channels)
                if device_info['maxOutputChannels'] > 0:
                    output_devices.append((i, device_name))
                
                # Find personal mic
                if self.personal_mic_name.lower() in device_name.lower() and device_info['maxInputChannels'] > 0:
                    personal_mic_candidates.append((i, device_name))
                
                # Find global mic
                if self.global_mic_name.lower() in device_name.lower() and device_info['maxInputChannels'] > 0:
                    global_mic_candidates.append((i, device_name))
            except Exception as e:
                print(f"  Error reading device {i}: {e}")
        
        # Select devices
        if output_devices:
            self.output_device_id = output_devices[0][0]  # Use first output device
            print(f"Selected output device: {output_devices[0][1]} (ID: {self.output_device_id})")
        else:
            print("No output device found!")
            return False
            
        if personal_mic_candidates:
            self.personal_mic_id = personal_mic_candidates[0][0]
            print(f"Selected personal mic: {personal_mic_candidates[0][1]} (ID: {self.personal_mic_id})")
        else:
            print(f"Personal mic '{self.personal_mic_name}' not found")
            # Use default input device as fallback
            try:
                default_input = self.pa.get_default_input_device_info()
                self.personal_mic_id = default_input['index']
                print(f"Using default input device as personal mic: {default_input['name']} (ID: {self.personal_mic_id})")
            except Exception as e:
                print(f"Error getting default input device: {e}")
                return False
            
        if global_mic_candidates:
            self.global_mic_id = global_mic_candidates[0][0]
            print(f"Selected global mic: {global_mic_candidates[0][1]} (ID: {self.global_mic_id})")
        else:
            print(f"Global mic '{self.global_mic_name}' not found")
            # If global mic isn't found but personal mic is available, use it as fallback
            if self.personal_mic_id is not None:
                self.global_mic_id = self.personal_mic_id
                print(f"Using personal mic as global mic fallback")
            else:
                return False
                
        return True
    
    def _init_network(self) -> bool:
        """Initialize network sockets for audio streaming."""
        try:
            # Create sender socket
            self.sender_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            
            # Create receiver socket
            self.receiver_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.receiver_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.receiver_socket.bind(("0.0.0.0", AUDIO_STREAM_PORT))
            self.receiver_socket.settimeout(0.5)  # Set timeout for responsive shutdown
            
            print(f"Audio network initialized on port {AUDIO_STREAM_PORT}")
            return True
        except Exception as e:
            print(f"Error initializing audio network: {e}")
            return False
    
    def _init_audio_streams(self) -> bool:
        """Initialize audio input and output streams with robust error handling."""
        try:
            # Initialize output stream (always running)
            try:
                # First attempt with standard settings
                self.output_stream = self.pa.open(
                    format=self.format,
                    channels=self.channels,
                    rate=self.rate,
                    output=True,
                    output_device_index=self.output_device_id,
                    frames_per_buffer=self.chunk_size
                )
            except Exception as e:
                print(f"Standard output stream failed: {e}")
                print("Trying with alternative output stream settings...")
                
                # Try with smaller buffer and different settings
                self.output_stream = self.pa.open(
                    format=self.format,
                    channels=self.channels,
                    rate=self.rate,
                    output=True,
                    output_device_index=self.output_device_id,
                    frames_per_buffer=512  # Smaller buffer
                )
            
            # Start with no input stream - will be opened based on state
            self.input_stream = None
            
            print("Audio streams initialized")
            return True
        except Exception as e:
            print(f"Error initializing audio streams: {e}")
            return False
    
    def _open_input_stream(self, device_id: int) -> bool:
        """Open an input stream for the specified device with fallback options."""
        # Close existing input stream if it's open
        if self.input_stream is not None:
            try:
                self.input_stream.stop_stream()
                self.input_stream.close()
            except Exception as e:
                print(f"Error closing existing input stream: {e}")
            self.input_stream = None
        
        try:
            # Get device info to check actual channel count
            device_info = self.pa.get_device_info_by_index(device_id)
            input_channels = min(1, int(device_info['maxInputChannels']))  # Use 1 channel max for input
            
            print(f"Opening input stream for device {device_id} with {input_channels} channel(s)")
            
            # Try with default settings first
            try:
                self.input_stream = self.pa.open(
                    format=self.format,
                    channels=input_channels,
                    rate=self.rate,
                    input=True,
                    input_device_index=device_id,
                    frames_per_buffer=self.chunk_size
                )
                print(f"Opened input stream for device ID {device_id}")
                return True
            except Exception as e1:
                print(f"First attempt failed: {e1}")
                print("Trying with alternative settings...")
                
                # Try with smaller buffer size and different stream parameters
                try:
                    self.input_stream = self.pa.open(
                        format=self.format,
                        channels=input_channels,
                        rate=self.rate,
                        input=True,
                        input_device_index=device_id,
                        frames_per_buffer=512,  # Smaller buffer
                        stream_callback=None,   # No callback
                        start=False             # Don't start yet
                    )
                    # Start manually
                    self.input_stream.start_stream()
                    print(f"Opened input stream with alternative settings for device ID {device_id}")
                    return True
                except Exception as e2:
                    print(f"Second attempt failed: {e2}")
                    raise  # Re-raise to be caught by outer exception handler
                    
        except Exception as e:
            print(f"Error opening input stream for device {device_id}: {e}")
            return False
    
    def _on_state_change(self, changed_state: str) -> None:
        """Handle system state changes."""
        if changed_state in ["local", "remote", "connection"]:
            self._update_audio_routing()
    
    def _update_audio_routing(self) -> None:
        """Update audio routing based on current system state."""
        local_state = system_state.get_local_state()
        remote_state = system_state.get_remote_state()
        
        # Only proceed if remote is connected
        if not remote_state.get("connected", False):
            print("Remote not connected, pausing audio streaming")
            self.is_sending = False
            self.current_mic = None
            self.muted_channel = 'both'  # Mute both channels
            self._update_system_state()
            return
        
        # Logic based on pressure states
        old_mic = self.current_mic
        
        # 1. When both have pressure: Play with LEFT channel muted, Send personal mic to remote
        if local_state.get("pressure", False) and remote_state.get("pressure", False):
            self.is_sending = True
            self.current_mic = 'personal'
            self.muted_channel = self.personal_speaker_mute_channel
            print("Audio: Both have pressure - Personal mic active, LEFT channel muted")
                
        # 2. When remote has pressure and local doesn't: Play with RIGHT channel muted, Send global mic to remote
        elif remote_state.get("pressure", False) and not local_state.get("pressure", False):
            self.is_sending = True
            self.current_mic = 'global'
            self.muted_channel = self.global_speaker_mute_channel
            print("Audio: Remote has pressure - Global mic active, RIGHT channel muted")
                
        # 3. When local has pressure and remote doesn't: Play with LEFT channel muted, Send personal mic to remote
        elif local_state.get("pressure", False) and not remote_state.get("pressure", False):
            self.is_sending = True
            self.current_mic = 'personal'
            self.muted_channel = self.personal_speaker_mute_channel
            print("Audio: Local has pressure - Personal mic active, LEFT channel muted")
                
        # 4. When neither has pressure: No playback
        else:
            self.is_sending = False
            self.current_mic = None
            self.muted_channel = 'both'  # Mute both channels
            print("Audio: No pressure - No audio transmission, both channels muted")
        
        # If the microphone changed, open the new input stream
        if self.current_mic != old_mic:
            if self.current_mic == 'personal':
                self._open_input_stream(self.personal_mic_id)
            elif self.current_mic == 'global':
                self._open_input_stream(self.global_mic_id)
            elif self.current_mic is None and self.input_stream is not None:
                # Close input stream when not needed
                try:
                    self.input_stream.stop_stream()
                    self.input_stream.close()
                except:
                    pass
                self.input_stream = None
        
        # Update system state with audio state
        self._update_system_state()
    
    def _update_system_state(self) -> None:
        """Update system state with current audio state."""
        audio_state = {
            "audio_sending": self.is_sending,
            "audio_mic": self.current_mic if self.current_mic else "None",
            "audio_muted_channel": self.muted_channel
        }
        system_state.update_audio_state(audio_state)
    
    def _audio_sender_loop(self) -> None:
        """Thread function for sending audio data with robust error handling."""
        print("Audio sender thread started")
        
        packet_counter = 0
        last_report_time = time.time()
        consecutive_errors = 0
        max_consecutive_errors = 5
        
        while not self.stop_event.is_set():
            if not self.is_sending or self.input_stream is None:
                time.sleep(0.1)
                consecutive_errors = 0  # Reset error counter during idle time
                continue
            
            try:
                # Read data from input stream with error handling
                try:
                    input_data = self.input_stream.read(self.chunk_size, exception_on_overflow=False)
                    consecutive_errors = 0  # Reset on successful read
                except Exception as read_error:
                    consecutive_errors += 1
                    print(f"Error reading from audio input: {read_error}")
                    
                    # If we have too many consecutive errors, try to reopen the stream
                    if consecutive_errors >= max_consecutive_errors:
                        print(f"Too many consecutive errors ({consecutive_errors}), attempting to reopen stream")
                        device_id = self.current_mic == 'personal' and self.personal_mic_id or self.global_mic_id
                        if device_id is not None:
                            self._open_input_stream(device_id)
                        consecutive_errors = 0
                    
                    time.sleep(0.1)
                    continue
                
                if input_data:
                    # Send audio data to remote
                    self.sender_socket.sendto(input_data, (self.remote_ip, AUDIO_STREAM_PORT))
                    
                    # Report stats periodically
                    packet_counter += 1
                    if packet_counter % 1000 == 0:
                        now = time.time()
                        elapsed = now - last_report_time
                        rate = 1000 / elapsed if elapsed > 0 else 0
                        print(f"Audio sender: sent {packet_counter} packets, {rate:.1f} packets/second")
                        last_report_time = now
                
            except Exception as e:
                if self.running and not self.stop_event.is_set():
                    print(f"Error in audio sender: {e}")
                time.sleep(0.1)
        
        print("Audio sender thread stopped")
    
    def _audio_receiver_loop(self) -> None:
        """Thread function for receiving audio data."""
        print("Audio receiver thread started")
        
        packet_counter = 0
        last_report_time = time.time()
        
        while not self.stop_event.is_set():
            try:
                # Receive audio data from network
                data, addr = self.receiver_socket.recvfrom(self.buffer_size)
                
                if data and self.output_stream is not None:
                    # Convert bytes to numpy array
                    # Try to determine if it's mono or stereo data
                    try:
                        samples = len(data) // 2  # 2 bytes per sample for 16-bit audio
                        if samples % 2 == 0:  # Might be stereo (2 channels)
                            # Make a copy to ensure array is writable
                            audio_data = np.frombuffer(data, dtype=np.int16).reshape(-1, 2).copy()
                        else:  # Must be mono (1 channel)
                            # Make a copy to ensure array is writable
                            mono_data = np.frombuffer(data, dtype=np.int16).copy()
                            # Duplicate mono to stereo for output
                            audio_data = np.column_stack((mono_data, mono_data))
                            
                        # Apply channel muting based on current state
                        if self.muted_channel == 'left':
                            audio_data[:, 0] = 0  # Mute left channel
                        elif self.muted_channel == 'right':
                            audio_data[:, 1] = 0  # Mute right channel
                        elif self.muted_channel == 'both':
                            audio_data[:, :] = 0  # Mute both channels
                        
                        # Write processed audio to output stream
                        self.output_stream.write(audio_data.tobytes())
                        
                    except Exception as processing_error:
                        print(f"Error processing audio data: {processing_error}")
                        # Fall back to raw data
                        self.output_stream.write(data)
                    
                    # Report stats periodically
                    packet_counter += 1
                    if packet_counter % 1000 == 0:
                        now = time.time()
                        elapsed = now - last_report_time
                        rate = 1000 / elapsed if elapsed > 0 else 0
                        print(f"Audio receiver: received {packet_counter} packets, {rate:.1f} packets/second")
                        last_report_time = now
            except socket.timeout:
                # This is expected due to the socket timeout
                pass
            except Exception as e:
                if self.running and not self.stop_event.is_set():
                    print(f"Error in audio receiver: {e}")
                time.sleep(0.1)
        
        print("Audio receiver thread stopped")


# Test function to run the audio system standalone
def test_audio_system():
    """Test the audio system with simulated pressure changes."""
    import time
    
    # Set up initial system state
    system_state.update_local_state({"pressure": False})
    system_state.update_remote_state({"pressure": False, "connected": True})
    
    # Initialize audio system
    audio_system = AudioSystem("127.0.0.1")  # Use loopback for testing
    if not audio_system.start():
        print("Failed to start audio system")
        return
    
    try:
        print("\nTesting different pressure states:")
        
        print("\n1. No pressure on either device - Should mute both channels")
        time.sleep(5)
        
        print("\n2. Remote has pressure - Should use global mic and mute RIGHT channel")
        system_state.update_remote_state({"pressure": True, "connected": True})
        time.sleep(5)
        
        print("\n3. Both have pressure - Should use personal mic and mute LEFT channel")
        system_state.update_local_state({"pressure": True})
        time.sleep(5)
        
        print("\n4. Local has pressure, remote doesn't - Should use personal mic and mute LEFT channel")
        system_state.update_remote_state({"pressure": False, "connected": True})
        time.sleep(5)
        
        print("\n5. Back to no pressure - Should mute both channels")
        system_state.update_local_state({"pressure": False})
        
        print("\nAudio test complete. Press Ctrl+C to exit.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Test interrupted by user")
    finally:
        # Clean up
        audio_system.stop()


# Run test if executed directly
if __name__ == "__main__":
    test_audio_system()
