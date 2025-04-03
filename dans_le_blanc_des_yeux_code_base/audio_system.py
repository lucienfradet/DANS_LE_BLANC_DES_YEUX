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

# Note: You may need to update system_state.py to add audio state support
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
        self.output_channels = 2      # Stereo output
        self.input_channels = 1       # Mono input (will detect from device)
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
                    output_devices.append((i, device_name, device_info['maxOutputChannels']))
                
                # Find personal mic
                if self.personal_mic_name.lower() in device_name.lower() and device_info['maxInputChannels'] > 0:
                    personal_mic_candidates.append((i, device_name, device_info['maxInputChannels']))
                
                # Find global mic
                if self.global_mic_name.lower() in device_name.lower() and device_info['maxInputChannels'] > 0:
                    global_mic_candidates.append((i, device_name, device_info['maxInputChannels']))
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
            # Store input channel count
            self.personal_mic_channels = personal_mic_candidates[0][2]
            print(f"Selected personal mic: {personal_mic_candidates[0][1]} (ID: {self.personal_mic_id}, Channels: {self.personal_mic_channels})")
        else:
            print(f"Personal mic '{self.personal_mic_name}' not found")
            # Use default input device as fallback
            try:
                default_input = self.pa.get_default_input_device_info()
                self.personal_mic_id = default_input['index']
                self.personal_mic_channels = default_input['maxInputChannels']
                print(f"Using default input device as personal mic: {default_input['name']} (ID: {self.personal_mic_id}, Channels: {self.personal_mic_channels})")
            except Exception as e:
                print(f"Error getting default input device: {e}")
                return False
            
        if global_mic_candidates:
            self.global_mic_id = global_mic_candidates[0][0]
            # Store input channel count
            self.global_mic_channels = global_mic_candidates[0][2]
            print(f"Selected global mic: {global_mic_candidates[0][1]} (ID: {self.global_mic_id}, Channels: {self.global_mic_channels})")
        else:
            print(f"Global mic '{self.global_mic_name}' not found")
            # If global mic isn't found but personal mic is available, use it as fallback
            if self.personal_mic_id is not None:
                self.global_mic_id = self.personal_mic_id
                self.global_mic_channels = self.personal_mic_channels
                print(f"Using personal mic as global mic fallback (Channels: {self.global_mic_channels})")
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
            
            # Set buffer size for better performance
            self.receiver_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)
            self.sender_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)
            
            # Bind to port
            self.receiver_socket.bind(("0.0.0.0", AUDIO_STREAM_PORT))
            self.receiver_socket.settimeout(0.5)  # Set timeout for responsive shutdown
            
            print(f"Audio network initialized on port {AUDIO_STREAM_PORT}")
            
            # Additional audio setup for mixer/volume
            self._setup_audio_mixer()
            
            return True
        except Exception as e:
            print(f"Error initializing audio network: {e}")
            return False
    
    def _setup_audio_mixer(self) -> None:
        """Try to setup ALSA mixer (volume) settings."""
        try:
            if os.system("which amixer > /dev/null") == 0:
                # Set master volume to 80%
                os.system("amixer set Master 80% unmute > /dev/null 2>&1")
                # Try to unmute specific devices 
                if self.personal_mic_name == "TX 96Khz":
                    os.system("amixer -c 3 set 'Speaker' 80% unmute > /dev/null 2>&1")
                    os.system("amixer -c 3 set 'Mic' 80% unmute > /dev/null 2>&1")
                print("Set audio mixer levels")
        except Exception as e:
            print(f"Could not setup audio mixer: {e}")
            # Non-critical error, continue anyway
    
    def _init_audio_streams(self) -> bool:
        """Initialize audio input and output streams."""
        try:
            # Initialize output stream (always running)
            self.output_stream = self.pa.open(
                format=self.format,
                channels=self.output_channels,  # Always stereo output
                rate=self.rate,
                output=True,
                output_device_index=self.output_device_id,
                frames_per_buffer=self.chunk_size
            )
            
            # Start with no input stream - will be opened based on state
            self.input_stream = None
            
            print("Audio streams initialized")
            return True
        except Exception as e:
            print(f"Error initializing audio streams: {e}")
            return False
    
    def _open_input_stream(self, device_id: int) -> bool:
        """Open an input stream for the specified device."""
        # Close existing input stream if it's open
        if self.input_stream is not None:
            try:
                self.input_stream.stop_stream()
                self.input_stream.close()
            except Exception as e:
                print(f"Error closing existing input stream: {e}")
            self.input_stream = None
        
        try:
            # Determine the number of input channels to use
            num_channels = 1  # Default to mono input
            
            # Check if this is personal or global mic and use stored channels info
            if device_id == self.personal_mic_id:
                num_channels = self.personal_mic_channels
                print(f"Using {num_channels} channel(s) for personal mic")
            elif device_id == self.global_mic_id:
                num_channels = self.global_mic_channels
                print(f"Using {num_channels} channel(s) for global mic")
            
            # Open new input stream
            self.input_stream = self.pa.open(
                format=self.format,
                channels=num_channels,  # Use actual number of channels the device supports
                rate=self.rate,
                input=True,
                input_device_index=device_id,
                frames_per_buffer=self.chunk_size
            )
            
            # Store the current input channels for processing
            self.current_input_channels = num_channels
            
            print(f"Opened input stream for device ID {device_id} with {num_channels} channel(s)")
            return True
        except Exception as e:
            print(f"Error opening input stream for device {device_id}: {e}")
            # Try with reduced settings as fallback
            try:
                print("Trying fallback configuration...")
                self.input_stream = self.pa.open(
                    format=self.format,
                    channels=1,  # Force mono
                    rate=22050,  # Lower sample rate
                    input=True,
                    input_device_index=device_id,
                    frames_per_buffer=512  # Smaller buffer
                )
                self.current_input_channels = 1
                print(f"Opened input stream with fallback settings (mono, 22kHz)")
                return True
            except Exception as fallback_error:
                print(f"Fallback also failed: {fallback_error}")
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
        
        # Add device details for debugging
        if hasattr(self, 'personal_mic_id') and self.personal_mic_id is not None:
            audio_state["personal_mic_id"] = self.personal_mic_id
        if hasattr(self, 'global_mic_id') and self.global_mic_id is not None:
            audio_state["global_mic_id"] = self.global_mic_id
        if hasattr(self, 'output_device_id') and self.output_device_id is not None:
            audio_state["output_device_id"] = self.output_device_id
        
        system_state.update_audio_state(audio_state)
    
    def _audio_sender_loop(self) -> None:
        """Thread function for sending audio data."""
        print("Audio sender thread started")
        
        packet_counter = 0
        last_report_time = time.time()
        
        while not self.stop_event.is_set():
            if not self.is_sending or self.input_stream is None:
                time.sleep(0.1)
                continue
            
            try:
                # Read data from input stream
                input_data = self.input_stream.read(self.chunk_size, exception_on_overflow=False)
                
                if input_data:
                    # Convert mono to stereo if needed (we need to send stereo for consistent processing)
                    if hasattr(self, 'current_input_channels') and self.current_input_channels == 1:
                        # Convert mono audio to stereo
                        mono_data = np.frombuffer(input_data, dtype=np.int16)
                        stereo_data = np.column_stack((mono_data, mono_data))  # Duplicate mono to both channels
                        input_data = stereo_data.tobytes()
                        
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
                    import traceback
                    traceback.print_exc()
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
                    try:
                        # Convert bytes to numpy array for processing
                        # Ensure we're getting stereo data
                        audio_data = np.frombuffer(data, dtype=np.int16)
                        
                        # Make sure we can reshape it to stereo
                        if len(audio_data) % 2 == 0:
                            audio_data = audio_data.reshape(-1, 2)
                            
                            # Apply channel muting based on current state
                            if self.muted_channel == 'left':
                                audio_data[:, 0] = 0  # Mute left channel
                                print("Muting LEFT channel") if packet_counter % 500 == 0 else None
                            elif self.muted_channel == 'right':
                                audio_data[:, 1] = 0  # Mute right channel
                                print("Muting RIGHT channel") if packet_counter % 500 == 0 else None
                            elif self.muted_channel == 'both':
                                audio_data[:, :] = 0  # Mute both channels
                                print("Muting BOTH channels") if packet_counter % 500 == 0 else None
                            
                            # Apply volume amplification (optional)
                            # Uncomment if you need to boost volume
                            # audio_data = np.clip(audio_data * 2, -32768, 32767).astype(np.int16)
                            
                            # Write processed audio to output stream
                            self.output_stream.write(audio_data.tobytes())
                        else:
                            print(f"Received odd-length audio data: {len(audio_data)} samples")
                        
                        # Report stats periodically
                        packet_counter += 1
                        if packet_counter % 1000 == 0:
                            now = time.time()
                            elapsed = now - last_report_time
                            rate = 1000 / elapsed if elapsed > 0 else 0
                            print(f"Audio receiver: received {packet_counter} packets, {rate:.1f} packets/second")
                            last_report_time = now
                    except Exception as process_error:
                        print(f"Error processing audio data: {process_error}")
            except socket.timeout:
                # This is expected due to the socket timeout
                pass
            except Exception as e:
                if self.running and not self.stop_event.is_set():
                    print(f"Error in audio receiver: {e}")
                    import traceback
                    traceback.print_exc()
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
