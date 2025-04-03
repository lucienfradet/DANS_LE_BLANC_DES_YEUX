"""
Audio system for Dans le Blanc des Yeux installation.
Handles audio streaming between devices and channel muting based on pressure states.
"""

import time
import threading
import socket
import numpy as np
import pyaudio
import configparser
from typing import Dict, Any, Optional, List, Tuple

from system_state import system_state

# Constants for audio streaming
CHUNK_SIZE = 1024
FORMAT = pyaudio.paInt16
OUTPUT_CHANNELS = 2  # Stereo output
RATE = 44100
AUDIO_PORT = 5002  # Port for audio streaming

class AudioSystem:
    """Handles audio capture, playback, and streaming between devices."""
    
    def __init__(self, remote_ip: str):
        self.remote_ip = remote_ip
        
        # PyAudio instance
        self.p = pyaudio.PyAudio()
        
        # Audio devices
        self.personal_mic_id = None
        self.personal_mic_channels = 1  # Default to mono
        self.global_mic_id = None
        self.global_mic_channels = 1  # Default to mono
        self.output_device_id = None
        
        # Channel muting configuration
        self.global_speaker_mute_channel = None  # 'left' or 'right'
        self.personal_speaker_mute_channel = None  # 'left' or 'right'
        
        # Audio streams
        self.personal_mic_stream = None
        self.global_mic_stream = None
        self.output_stream = None
        
        # Audio buffers for sending and receiving
        self.personal_mic_buffer = np.zeros((CHUNK_SIZE, CHANNELS), dtype=np.int16)
        self.global_mic_buffer = np.zeros((CHUNK_SIZE, CHANNELS), dtype=np.int16)
        self.received_audio_buffer = np.zeros((CHUNK_SIZE, CHANNELS), dtype=np.int16)
        
        # Buffer locks
        self.personal_mic_lock = threading.Lock()
        self.global_mic_lock = threading.Lock()
        self.received_audio_lock = threading.Lock()
        
        # Streaming sockets
        self.send_socket = None
        self.receive_socket = None
        
        # Thread control
        self.running = False
        self.threads = []
        self.lock = threading.Lock()
        
        # Audio state
        self.streaming_personal_mic = False
        self.streaming_global_mic = False
        self.playing_audio = False
        self.muted_channels = {"left": False, "right": False}
        
        # Load configuration
        self._load_config()
        
        # Register as observer for state changes
        system_state.add_observer(self._on_state_change)
        
        print(f"Audio system initialized with remote IP: {remote_ip}")
    
    def _load_config(self):
        """Load audio configuration from config.ini."""
        try:
            config = configparser.ConfigParser()
            config.read('config.ini')
            
            if 'audio' in config:
                # Load speaker channel muting configuration
                self.global_speaker_mute_channel = config.get('audio', 'global_speaker_mute_channel', fallback='right')
                self.personal_speaker_mute_channel = config.get('audio', 'personal_speaker_mute_channel', fallback='left')
                
                # Device names from config
                personal_mic_name = config.get('audio', 'personal_mic_name', fallback='TX 96Khz')
                global_mic_name = config.get('audio', 'global_mic_name', fallback='USB Audio Device')
                
                print(f"Using audio configuration:")
                print(f"  Global speaker mute channel: {self.global_speaker_mute_channel}")
                print(f"  Personal speaker mute channel: {self.personal_speaker_mute_channel}")
                print(f"  Personal mic name: {personal_mic_name}")
                print(f"  Global mic name: {global_mic_name}")
                
                # Find device IDs by name
                self._find_audio_devices(personal_mic_name, global_mic_name)
            else:
                print("No [audio] section found in config.ini, using default settings")
                # Use default names for device detection
                self._find_audio_devices("TX 96Khz", "USB Audio Device")
        except Exception as e:
            print(f"Error loading audio config: {e}")
            print("Using default audio settings")
            # Use default names for device detection
            self._find_audio_devices("TX 96Khz", "USB Audio Device")
    
    def _find_audio_devices(self, personal_mic_name: str, global_mic_name: str):
        """Find audio device IDs based on device names."""
        try:
            # Print all available devices for debugging
            print("\nAvailable audio devices:")
            for i in range(self.p.get_device_count()):
                dev_info = self.p.get_device_info_by_index(i)
                print(f"  {i}: {dev_info['name']} (in: {dev_info['maxInputChannels']}, out: {dev_info['maxOutputChannels']})")
            
            # Find personal mic
            for i in range(self.p.get_device_count()):
                dev_info = self.p.get_device_info_by_index(i)
                if personal_mic_name.lower() in dev_info['name'].lower() and dev_info['maxInputChannels'] > 0:
                    self.personal_mic_id = i
                    self.personal_mic_channels = int(dev_info['maxInputChannels'])
                    print(f"Found personal mic: {dev_info['name']} (ID: {i}, Channels: {self.personal_mic_channels})")
                    break
            
            # Find global mic
            for i in range(self.p.get_device_count()):
                dev_info = self.p.get_device_info_by_index(i)
                if global_mic_name.lower() in dev_info['name'].lower() and dev_info['maxInputChannels'] > 0:
                    self.global_mic_id = i
                    self.global_mic_channels = int(dev_info['maxInputChannels'])
                    print(f"Found global mic: {dev_info['name']} (ID: {i}, Channels: {self.global_mic_channels})")
                    break
            
            # Find output device (use default output)
            self.output_device_id = self.p.get_default_output_device_info()['index']
            print(f"Using default output device: {self.p.get_device_info_by_index(self.output_device_id)['name']} (ID: {self.output_device_id})")
            
            # Check if we found all needed devices
            if self.personal_mic_id is None:
                print(f"Warning: Could not find personal mic with name '{personal_mic_name}'")
                # Try to use default input device
                default_input = self.p.get_default_input_device_info()['index']
                print(f"Using default input device for personal mic: {self.p.get_device_info_by_index(default_input)['name']} (ID: {default_input})")
                self.personal_mic_id = default_input
            
            if self.global_mic_id is None:
                print(f"Warning: Could not find global mic with name '{global_mic_name}'")
                if self.personal_mic_id is not None:
                    print(f"Using personal mic for global mic as well")
                    self.global_mic_id = self.personal_mic_id
                else:
                    # Try to use default input device
                    default_input = self.p.get_default_input_device_info()['index']
                    print(f"Using default input device for global mic: {self.p.get_device_info_by_index(default_input)['name']} (ID: {default_input})")
                    self.global_mic_id = default_input
        except Exception as e:
            print(f"Error finding audio devices: {e}")
            print("Audio functionality may be limited")
    
    def start(self) -> bool:
        """Start the audio system."""
        print("Starting audio system...")
        
        try:
            # Create UDP sockets for audio streaming
            self.send_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.receive_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.receive_socket.bind(("0.0.0.0", AUDIO_PORT))
            self.receive_socket.settimeout(0.5)
            
            # Initialize audio streams
            if not self._start_audio_streams():
                print("Failed to start audio streams")
                return False
            
            self.running = True
            
            # Start audio processing threads
            
            # Start receiver thread
            receiver_thread = threading.Thread(target=self._audio_receiver_loop)
            receiver_thread.daemon = True
            receiver_thread.start()
            self.threads.append(receiver_thread)
            
            # Start personal mic sender thread
            personal_sender_thread = threading.Thread(target=self._personal_mic_sender_loop)
            personal_sender_thread.daemon = True
            personal_sender_thread.start()
            self.threads.append(personal_sender_thread)
            
            # Start global mic sender thread
            global_sender_thread = threading.Thread(target=self._global_mic_sender_loop)
            global_sender_thread.daemon = True
            global_sender_thread.start()
            self.threads.append(global_sender_thread)
            
            # Start playback thread
            playback_thread = threading.Thread(target=self._audio_playback_loop)
            playback_thread.daemon = True
            playback_thread.start()
            self.threads.append(playback_thread)
            
            # Check initial state to see if we need to start streaming right away
            self._update_audio_based_on_state()
            
            print("Audio system started")
            return True
        except Exception as e:
            print(f"Error starting audio system: {e}")
            self.stop()
            return False
    
    def stop(self) -> None:
        """Stop the audio system and release resources."""
        print("Stopping audio system...")
        self.running = False
        
        # Wait for threads to finish
        for thread in self.threads:
            thread.join(timeout=1.0)
        
        # Close audio streams
        self._close_audio_streams()
        
        # Close sockets
        if self.send_socket:
            self.send_socket.close()
        if self.receive_socket:
            self.receive_socket.close()
        
        # Terminate PyAudio
        if self.p:
            self.p.terminate()
        
        print("Audio system stopped")
    
    def _start_audio_streams(self) -> bool:
        """Initialize all audio streams (inputs and outputs)."""
        try:
            # Start personal mic stream (input)
            if self.personal_mic_id is not None:
                self.personal_mic_stream = self.p.open(
                    format=FORMAT,
                    channels=self.personal_mic_channels,  # Use the actual number of channels
                    rate=RATE,
                    input=True,
                    input_device_index=self.personal_mic_id,
                    frames_per_buffer=CHUNK_SIZE,
                    stream_callback=self._personal_mic_callback
                )
                self.personal_mic_stream.start_stream()
                print(f"Started personal mic stream (ID: {self.personal_mic_id}, Channels: {self.personal_mic_channels})")
            else:
                print("No personal mic device found, personal mic functionality will be disabled")
            
            # Start global mic stream (input)
            if self.global_mic_id is not None:
                self.global_mic_stream = self.p.open(
                    format=FORMAT,
                    channels=self.global_mic_channels,  # Use the actual number of channels
                    rate=RATE,
                    input=True,
                    input_device_index=self.global_mic_id,
                    frames_per_buffer=CHUNK_SIZE,
                    stream_callback=self._global_mic_callback
                )
                self.global_mic_stream.start_stream()
                print(f"Started global mic stream (ID: {self.global_mic_id}, Channels: {self.global_mic_channels})")
            else:
                print("No global mic device found, global mic functionality will be disabled")
            
            # Start output stream
            self.output_stream = self.p.open(
                format=FORMAT,
                channels=OUTPUT_CHANNELS,  # Always stereo for output
                rate=RATE,
                output=True,
                output_device_index=self.output_device_id,
                frames_per_buffer=CHUNK_SIZE
            )
            print(f"Started output stream (ID: {self.output_device_id})")
            
            return True
        except Exception as e:
            print(f"Error starting audio streams: {e}")
            self._close_audio_streams()
            return False
    
    def _close_audio_streams(self) -> None:
        """Close all audio streams."""
        # Close personal mic stream
        if self.personal_mic_stream:
            try:
                self.personal_mic_stream.stop_stream()
                self.personal_mic_stream.close()
            except Exception as e:
                print(f"Error closing personal mic stream: {e}")
            self.personal_mic_stream = None
        
        # Close global mic stream
        if self.global_mic_stream:
            try:
                self.global_mic_stream.stop_stream()
                self.global_mic_stream.close()
            except Exception as e:
                print(f"Error closing global mic stream: {e}")
            self.global_mic_stream = None
        
        # Close output stream
        if self.output_stream:
            try:
                self.output_stream.stop_stream()
                self.output_stream.close()
            except Exception as e:
                print(f"Error closing output stream: {e}")
            self.output_stream = None
    
    def _personal_mic_callback(self, in_data, frame_count, time_info, status):
        """Callback for personal mic stream."""
        if in_data:
            # Convert bytes to numpy array
            try:
                # Handle mono vs stereo input
                if self.personal_mic_channels == 1:
                    # Convert mono to stereo
                    mono_data = np.frombuffer(in_data, dtype=np.int16)
                    # Create stereo data by duplicating mono channel
                    stereo_data = np.column_stack((mono_data, mono_data))
                    audio_data = stereo_data
                else:
                    # Already stereo
                    audio_data = np.frombuffer(in_data, dtype=np.int16).reshape(-1, self.personal_mic_channels)
                
                # Store in buffer for sending
                with self.personal_mic_lock:
                    self.personal_mic_buffer = audio_data.copy()
            except Exception as e:
                print(f"Error in personal mic callback: {e}")
        
        return (in_data, pyaudio.paContinue)
    
    def _global_mic_callback(self, in_data, frame_count, time_info, status):
        """Callback for global mic stream."""
        if in_data:
            # Convert bytes to numpy array
            try:
                # Handle mono vs stereo input
                if self.global_mic_channels == 1:
                    # Convert mono to stereo
                    mono_data = np.frombuffer(in_data, dtype=np.int16)
                    # Create stereo data by duplicating mono channel
                    stereo_data = np.column_stack((mono_data, mono_data))
                    audio_data = stereo_data
                else:
                    # Already stereo
                    audio_data = np.frombuffer(in_data, dtype=np.int16).reshape(-1, self.global_mic_channels)
                
                # Store in buffer for sending
                with self.global_mic_lock:
                    self.global_mic_buffer = audio_data.copy()
            except Exception as e:
                print(f"Error in global mic callback: {e}")
        
        return (in_data, pyaudio.paContinue)
    
    def _on_state_change(self, changed_state: str) -> None:
        """Handle system state changes."""
        if changed_state in ["local", "remote"]:
            self._update_audio_based_on_state()
    
    def _update_audio_based_on_state(self) -> None:
        """Update audio streaming and muting based on current state."""
        local_state = system_state.get_local_state()
        remote_state = system_state.get_remote_state()
        
        # Only proceed if remote is connected
        if not remote_state.get("connected", False):
            self._stop_all_audio()
            return
        
        # Case 1: Both have pressure
        if local_state.get("pressure", False) and remote_state.get("pressure", False):
            # Play with LEFT channel muted
            self._set_muted_channels(left=True, right=False)
            self.playing_audio = True
            
            # Stream personal mic to remote
            self.streaming_personal_mic = True
            self.streaming_global_mic = False
            
            # Update system state
            system_state.update_local_state({"audio": {
                "playing": True,
                "muted_channels": ["left"],
                "streaming_mic": "personal"
            }})
        
        # Case 2: Remote has pressure and local doesn't
        elif remote_state.get("pressure", False) and not local_state.get("pressure", False):
            # Play with RIGHT channel muted
            self._set_muted_channels(left=False, right=True)
            self.playing_audio = True
            
            # Stream global mic to remote
            self.streaming_personal_mic = False
            self.streaming_global_mic = True
            
            # Update system state
            system_state.update_local_state({"audio": {
                "playing": True,
                "muted_channels": ["right"],
                "streaming_mic": "global"
            }})
        
        # Case 3: Local has pressure and remote doesn't
        elif local_state.get("pressure", False) and not remote_state.get("pressure", False):
            # Play with LEFT channel muted
            self._set_muted_channels(left=True, right=False)
            self.playing_audio = True
            
            # Stream personal mic to remote
            self.streaming_personal_mic = True
            self.streaming_global_mic = False
            
            # Update system state
            system_state.update_local_state({"audio": {
                "playing": True,
                "muted_channels": ["left"],
                "streaming_mic": "personal"
            }})
        
        # Case 4: No pressure on either device
        else:
            # No playback
            self._stop_all_audio()
    
    def _stop_all_audio(self) -> None:
        """Stop all audio streaming and playback."""
        # Stop streaming
        self.streaming_personal_mic = False
        self.streaming_global_mic = False
        
        # Stop playback
        self.playing_audio = False
        
        # Mute all channels
        self._set_muted_channels(left=True, right=True)
        
        # Update system state
        system_state.update_local_state({"audio": {
            "playing": False,
            "muted_channels": ["left", "right"],
            "streaming_mic": "none"
        }})
    
    def _set_muted_channels(self, left: bool, right: bool) -> None:
        """Set the muting state for left and right channels."""
        with self.lock:
            self.muted_channels["left"] = left
            self.muted_channels["right"] = right
    
    def _mute_audio_channels(self, audio_data: np.ndarray) -> np.ndarray:
        """Mute specific channels in the audio data.
        
        Args:
            audio_data: Audio data as numpy array (shape: [samples, channels])
            
        Returns:
            Modified audio data with channels muted as needed
        """
        # Make a copy to avoid modifying the original data
        result = audio_data.copy()
        
        # Check if audio_data has the right shape
        if result.ndim != 2 or result.shape[1] < 2:
            # If mono or invalid, convert to stereo
            if result.ndim == 1:
                # Reshape mono to stereo
                result = np.column_stack((result, result))
            elif result.ndim == 2 and result.shape[1] == 1:
                # Add second channel
                result = np.column_stack((result.flatten(), result.flatten()))
            else:
                # Something's wrong, return original
                print(f"Warning: Unexpected audio data shape: {result.shape}")
                return result
        
        # Mute left channel if needed
        if self.muted_channels["left"]:
            result[:, 0] = 0
        
        # Mute right channel if needed
        if self.muted_channels["right"]:
            result[:, 1] = 0
        
        return result
    
    def _personal_mic_sender_loop(self) -> None:
        """Send personal mic audio to remote device when needed."""
        last_report_time = time.time()
        bytes_sent = 0
        
        while self.running:
            try:
                if not self.streaming_personal_mic or self.personal_mic_stream is None:
                    time.sleep(0.1)
                    continue
                
                # Get data from buffer
                with self.personal_mic_lock:
                    audio_data = self.personal_mic_buffer.copy()
                
                # Convert to bytes
                data = audio_data.tobytes()
                
                # Send data to remote device
                self.send_socket.sendto(data, (self.remote_ip, AUDIO_PORT))
                
                # Update stats
                bytes_sent += len(data)
                current_time = time.time()
                if current_time - last_report_time >= 10:
                    print(f"Personal mic: sent {bytes_sent/1024:.1f} KB in the last 10 seconds")
                    bytes_sent = 0
                    last_report_time = current_time
                
                # Sleep briefly to control data rate
                time.sleep(0.02)  # ~50Hz
                
            except Exception as e:
                print(f"Error in personal mic sender: {e}")
                time.sleep(1.0)
    
    def _global_mic_sender_loop(self) -> None:
        """Send global mic audio to remote device when needed."""
        last_report_time = time.time()
        bytes_sent = 0
        
        while self.running:
            try:
                if not self.streaming_global_mic or self.global_mic_stream is None:
                    time.sleep(0.1)
                    continue
                
                # Get data from buffer
                with self.global_mic_lock:
                    audio_data = self.global_mic_buffer.copy()
                
                # Convert to bytes
                data = audio_data.tobytes()
                
                # Send data to remote device
                self.send_socket.sendto(data, (self.remote_ip, AUDIO_PORT))
                
                # Update stats
                bytes_sent += len(data)
                current_time = time.time()
                if current_time - last_report_time >= 10:
                    print(f"Global mic: sent {bytes_sent/1024:.1f} KB in the last 10 seconds")
                    bytes_sent = 0
                    last_report_time = current_time
                
                # Sleep briefly to control data rate
                time.sleep(0.02)  # ~50Hz
                
            except Exception as e:
                print(f"Error in global mic sender: {e}")
                time.sleep(1.0)
    
    def _audio_receiver_loop(self) -> None:
        """Receive audio from remote device."""
        print(f"Starting audio receiver on port {AUDIO_PORT}")
        
        last_report_time = time.time()
        bytes_received = 0
        
        while self.running:
            try:
                # Receive audio data
                try:
                    data, addr = self.receive_socket.recvfrom(65536)
                    
                    # Convert bytes to numpy array
                    try:
                        # Try to interpret as stereo data first
                        audio_chunk = np.frombuffer(data, dtype=np.int16).reshape(-1, 2)
                    except ValueError:
                        # If that fails, it might be mono data
                        mono_data = np.frombuffer(data, dtype=np.int16)
                        # Convert mono to stereo
                        audio_chunk = np.column_stack((mono_data, mono_data))
                    
                    # Store in buffer for playback
                    with self.received_audio_lock:
                        self.received_audio_buffer = audio_chunk.copy()
                    
                    # Update stats
                    bytes_received += len(data)
                    current_time = time.time()
                    if current_time - last_report_time >= 10:
                        print(f"Audio receiver: received {bytes_received/1024:.1f} KB in the last 10 seconds")
                        bytes_received = 0
                        last_report_time = current_time
                except socket.timeout:
                    # This is expected due to the socket timeout
                    pass
                except Exception as e:
                    if self.running:
                        print(f"Error receiving audio: {e}")
                        time.sleep(0.5)
            except Exception as e:
                if self.running:
                    print(f"Error in audio receiver: {e}")
                    time.sleep(1.0)
    
    def _audio_playback_loop(self) -> None:
        """Play received audio with channel muting."""
        print("Starting audio playback loop")
        
        while self.running:
            try:
                if not self.playing_audio or self.output_stream is None:
                    time.sleep(0.1)
                    continue
                
                # Get the latest received audio
                with self.received_audio_lock:
                    audio_data = self.received_audio_buffer.copy()
                
                # Apply channel muting
                muted_audio = self._mute_audio_channels(audio_data)
                
                # Convert back to bytes
                output_data = muted_audio.tobytes()
                
                # Play audio
                self.output_stream.write(output_data)
            except Exception as e:
                print(f"Error in audio playback: {e}")
                time.sleep(0.5)

# Test function to run the audio system standalone
def test_audio_system():
    """Test the audio system by toggling different states."""
    from system_state import system_state
    
    # Set up system state for testing
    system_state.update_local_state({"pressure": False})
    system_state.update_remote_state({"pressure": True, "connected": True})
    
    # Initialize audio system with loopback address for testing
    audio_system = AudioSystem("127.0.0.1")
    if not audio_system.start():
        print("Failed to start audio system")
        return
    
    try:
        print("\nTesting different pressure states:")
        print("\n1. Remote pressure, local no pressure - RIGHT channel muted, streaming global mic")
        time.sleep(10)
        
        print("\n2. Both have pressure - LEFT channel muted, streaming personal mic")
        system_state.update_local_state({"pressure": True})
        time.sleep(10)
        
        print("\n3. Local pressure, remote no pressure - LEFT channel muted, streaming personal mic")
        system_state.update_remote_state({"pressure": False})
        time.sleep(10)
        
        print("\n4. Neither has pressure - No playback")
        system_state.update_local_state({"pressure": False})
        time.sleep(10)
        
        print("\nAudio system test complete.")
    except KeyboardInterrupt:
        print("Test interrupted by user")
    finally:
        # Clean up
        audio_system.stop()

# Run test if executed directly
if __name__ == "__main__":
    test_audio_system()
