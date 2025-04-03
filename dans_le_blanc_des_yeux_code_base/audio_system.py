"""
Audio system for Dans le Blanc des Yeux installation.
Uses VLC for reliable audio streaming and PyAudio for device management.
"""

import time
import threading
import subprocess
import socket
import numpy as np
import pyaudio
import configparser
import os
import signal
import atexit
from typing import Dict, Any, Optional, List, Tuple
from pydub import AudioSegment

from system_state import system_state

class AudioSystem:
    """Handles audio capture, playback, and streaming between devices using VLC."""
    
    def __init__(self, remote_ip: str):
        self.remote_ip = remote_ip
        
        # PyAudio instance
        self.p = None
        
        # Audio devices
        self.personal_mic_id = None
        self.personal_mic_name = None
        self.personal_mic_channels = 1  # TX mic is mono
        
        self.global_mic_id = None
        self.global_mic_name = None
        self.global_mic_channels = 1  # Default to mono, will detect
        
        self.output_device_id = None
        self.output_device_name = None
        
        # Channel muting configuration
        self.global_speaker_mute_channel = None  # 'left' or 'right'
        self.personal_speaker_mute_channel = None  # 'left' or 'right'
        
        # Output stream
        self.output_stream = None
        
        # Streaming subprocesses
        self.personal_mic_stream_process = None
        self.global_mic_stream_process = None
        self.receive_stream_process = None
        
        # Streaming control
        self.streaming_personal_mic = False
        self.streaming_global_mic = False
        self.playing_audio = False
        self.stream_port = 8888
        self.receive_port = 8889
        
        # Muted channels
        self.muted_channels = {"left": False, "right": False}
        
        # Thread control
        self.running = False
        self.threads = []
        self.lock = threading.Lock()
        self.is_shutting_down = False
        
        # HTTP streaming URLs
        self.outgoing_stream_url = f"http://{self.remote_ip}:{self.stream_port}/audio.mp3"
        self.incoming_stream_url = f"http://0.0.0.0:{self.receive_port}/audio.mp3"
        
        # Load configuration
        self._load_config()
        
        # Register as observer for state changes
        system_state.add_observer(self._on_state_change)
        
        # Register cleanup handler
        atexit.register(self.stop)
        
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
                self.personal_mic_name = config.get('audio', 'personal_mic_name', fallback='TX 96Khz')
                self.global_mic_name = config.get('audio', 'global_mic_name', fallback='USB Audio Device')
                
                print(f"Using audio configuration:")
                print(f"  Global speaker mute channel: {self.global_speaker_mute_channel}")
                print(f"  Personal speaker mute channel: {self.personal_speaker_mute_channel}")
                print(f"  Personal mic name: {self.personal_mic_name}")
                print(f"  Global mic name: {self.global_mic_name}")
            else:
                print("No [audio] section found in config.ini, using default settings")
                # Use default names for device detection
                self.personal_mic_name = "TX 96Khz"
                self.global_mic_name = "USB Audio Device"
        except Exception as e:
            print(f"Error loading audio config: {e}")
            print("Using default audio settings")
            self.personal_mic_name = "TX 96Khz"
            self.global_mic_name = "USB Audio Device"
    
    def _find_audio_devices(self):
        """Find audio device IDs based on device names from config."""
        try:
            # Print all available devices for debugging
            print("\nAvailable audio devices:")
            for i in range(self.p.get_device_count()):
                dev_info = self.p.get_device_info_by_index(i)
                print(f"  {i}: {dev_info['name']} (in: {dev_info['maxInputChannels']}, out: {dev_info['maxOutputChannels']})")
            
            # Find personal mic
            for i in range(self.p.get_device_count()):
                dev_info = self.p.get_device_info_by_index(i)
                if self.personal_mic_name.lower() in dev_info['name'].lower() and dev_info['maxInputChannels'] > 0:
                    self.personal_mic_id = i
                    self.personal_mic_channels = int(dev_info['maxInputChannels'])
                    print(f"Found personal mic: {dev_info['name']} (ID: {i}, Channels: {self.personal_mic_channels})")
                    break
            
            # Find global mic
            for i in range(self.p.get_device_count()):
                dev_info = self.p.get_device_info_by_index(i)
                if self.global_mic_name.lower() in dev_info['name'].lower() and dev_info['maxInputChannels'] > 0:
                    self.global_mic_id = i
                    self.global_mic_channels = int(dev_info['maxInputChannels'])
                    print(f"Found global mic: {dev_info['name']} (ID: {i}, Channels: {self.global_mic_channels})")
                    break
            
            # Find output device (use default output)
            default_output = self.p.get_default_output_device_info()
            self.output_device_id = default_output['index']
            self.output_device_name = default_output['name']
            print(f"Using default output device: {self.output_device_name} (ID: {self.output_device_id})")
            
            # Check if we found all needed devices
            if self.personal_mic_id is None:
                print(f"Warning: Could not find personal mic with name '{self.personal_mic_name}'")
                # Try to use default input device
                default_input = self.p.get_default_input_device_info()
                print(f"Using default input device for personal mic: {default_input['name']} (ID: {default_input['index']})")
                self.personal_mic_id = default_input['index']
                self.personal_mic_name = default_input['name']
            
            if self.global_mic_id is None:
                print(f"Warning: Could not find global mic with name '{self.global_mic_name}'")
                if self.personal_mic_id is not None:
                    print(f"Using personal mic for global mic as well")
                    self.global_mic_id = self.personal_mic_id
                    self.global_mic_name = self.personal_mic_name
                    self.global_mic_channels = self.personal_mic_channels
                else:
                    # Try to use default input device
                    default_input = self.p.get_default_input_device_info()
                    print(f"Using default input device for global mic: {default_input['name']} (ID: {default_input['index']})")
                    self.global_mic_id = default_input['index']
                    self.global_mic_name = default_input['name']
                    self.global_mic_channels = int(default_input['maxInputChannels'])
            
            return True
        except Exception as e:
            print(f"Error finding audio devices: {e}")
            print("Audio functionality may be limited")
            return False
    
    def start(self) -> bool:
        """Start the audio system."""
        print("Starting audio system...")
        
        try:
            # Initialize PyAudio in the main thread
            self.p = pyaudio.PyAudio()
            
            # Find audio devices
            if not self._find_audio_devices():
                print("Failed to find audio devices")
                return False
            
            # Initialize audio output stream for playback
            self._init_output_stream()
            
            self.running = True
            self.is_shutting_down = False
            
            # Start audio playback thread
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
        if self.is_shutting_down:
            return
            
        print("Stopping audio system...")
        self.is_shutting_down = True
        self.running = False
        
        # Stop all streaming
        self._stop_all_audio()
        
        # Kill any VLC processes
        self._kill_vlc_processes()
        
        # Wait for threads to finish
        for thread in self.threads:
            thread.join(timeout=1.0)
        
        # Close output stream
        if self.output_stream:
            try:
                if self.output_stream.is_active():
                    self.output_stream.stop_stream()
                self.output_stream.close()
                self.output_stream = None
            except Exception as e:
                print(f"Error closing output stream: {e}")
        
        # Terminate PyAudio
        if self.p:
            try:
                self.p.terminate()
                self.p = None
            except Exception as e:
                print(f"Error terminating PyAudio: {e}")
        
        print("Audio system stopped")
    
    def _init_output_stream(self) -> bool:
        """Initialize the audio output stream."""
        try:
            # Start output stream
            self.output_stream = self.p.open(
                format=pyaudio.paInt16,
                channels=2,  # Always stereo for output
                rate=44100,
                output=True,
                output_device_index=self.output_device_id,
                frames_per_buffer=1024
            )
            print(f"Started output stream (ID: {self.output_device_id})")
            return True
        except Exception as e:
            print(f"Error starting output stream: {e}")
            return False
    
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
            self._start_personal_mic_stream()
            self._stop_global_mic_stream()
            
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
            self._stop_personal_mic_stream()
            self._start_global_mic_stream()
            
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
            self._start_personal_mic_stream()
            self._stop_global_mic_stream()
            
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
        self._stop_personal_mic_stream()
        self._stop_global_mic_stream()
        
        # Stop stream receiving
        self._stop_receive_stream()
        
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
            print(f"Audio channels muting: Left={left}, Right={right}")
    
    def _start_personal_mic_stream(self) -> None:
        """Start streaming personal mic using VLC."""
        if self.streaming_personal_mic:
            return
            
        if self.personal_mic_id is None:
            print("Cannot stream personal mic: No device found")
            return
        
        try:
            # Stop any existing process
            if self.personal_mic_stream_process:
                self._stop_personal_mic_stream()
            
            # Get the ALSA device name
            hw_device = f"hw:{self.personal_mic_id},0"
            print(f"Starting personal mic stream from {hw_device} to {self.outgoing_stream_url}")
            
            # Start VLC process for streaming
            cmd = [
                'cvlc',
                f'alsa://{hw_device}',
                '--sout', 
                f'#transcode{{acodec=mp3,ab=64,channels=1}}:http{{mux=mp3,dst={self.remote_ip}:{self.stream_port}/audio.mp3}}'
            ]
            
            self.personal_mic_stream_process = subprocess.Popen(
                cmd, 
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # Also start receiving stream from remote
            self._start_receive_stream()
            
            self.streaming_personal_mic = True
            print("Personal mic streaming started")
        except Exception as e:
            print(f"Error starting personal mic stream: {e}")
    
    def _stop_personal_mic_stream(self) -> None:
        """Stop streaming personal mic."""
        if not self.streaming_personal_mic:
            return
            
        if self.personal_mic_stream_process:
            try:
                self.personal_mic_stream_process.terminate()
                self.personal_mic_stream_process.wait(timeout=2)
            except Exception as e:
                print(f"Error stopping personal mic stream: {e}")
                # Force kill if needed
                try:
                    self.personal_mic_stream_process.kill()
                except:
                    pass
            self.personal_mic_stream_process = None
        
        self.streaming_personal_mic = False
        print("Personal mic streaming stopped")
    
    def _start_global_mic_stream(self) -> None:
        """Start streaming global mic using VLC."""
        if self.streaming_global_mic:
            return
            
        if self.global_mic_id is None:
            print("Cannot stream global mic: No device found")
            return
        
        try:
            # Stop any existing process
            if self.global_mic_stream_process:
                self._stop_global_mic_stream()
            
            # Get the ALSA device name
            hw_device = f"hw:{self.global_mic_id},0"
            print(f"Starting global mic stream from {hw_device} to {self.outgoing_stream_url}")
            
            # Start VLC process for streaming
            cmd = [
                'cvlc',
                f'alsa://{hw_device}',
                '--sout', 
                f'#transcode{{acodec=mp3,ab=64,channels=1}}:http{{mux=mp3,dst={self.remote_ip}:{self.stream_port}/audio.mp3}}'
            ]
            
            self.global_mic_stream_process = subprocess.Popen(
                cmd, 
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # Also start receiving stream from remote
            self._start_receive_stream()
            
            self.streaming_global_mic = True
            print("Global mic streaming started")
        except Exception as e:
            print(f"Error starting global mic stream: {e}")
    
    def _stop_global_mic_stream(self) -> None:
        """Stop streaming global mic."""
        if not self.streaming_global_mic:
            return
            
        if self.global_mic_stream_process:
            try:
                self.global_mic_stream_process.terminate()
                self.global_mic_stream_process.wait(timeout=2)
            except Exception as e:
                print(f"Error stopping global mic stream: {e}")
                # Force kill if needed
                try:
                    self.global_mic_stream_process.kill()
                except:
                    pass
            self.global_mic_stream_process = None
        
        self.streaming_global_mic = False
        print("Global mic streaming stopped")
    
    def _start_receive_stream(self) -> None:
        """Start receiving audio stream from remote device."""
        try:
            # Stop any existing receive process
            self._stop_receive_stream()
            
            # We'll start listening on our receive port for incoming audio
            print(f"Starting to receive stream from {self.remote_ip}")
            
            # Start VLC process for receiving and playing
            # The fifo file will be read by our audio playback thread
            fifo_path = "/tmp/dans_le_blanc_audio_fifo"
            
            # Create FIFO if it doesn't exist
            if not os.path.exists(fifo_path):
                try:
                    os.mkfifo(fifo_path)
                except Exception as e:
                    print(f"Error creating FIFO: {e}")
                    return
            
            # Start VLC to receive audio and write to FIFO
            cmd = [
                'cvlc',
                f'http://{self.remote_ip}:{self.stream_port}/audio.mp3',
                '--sout',
                f'#std{{access=file,mux=raw,dst={fifo_path}}}'
            ]
            
            self.receive_stream_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            print("Audio receive process started")
        except Exception as e:
            print(f"Error starting receive stream: {e}")
    
    def _stop_receive_stream(self) -> None:
        """Stop receiving audio stream."""
        if self.receive_stream_process:
            try:
                self.receive_stream_process.terminate()
                self.receive_stream_process.wait(timeout=2)
            except Exception as e:
                print(f"Error stopping receive stream: {e}")
                # Force kill if needed
                try:
                    self.receive_stream_process.kill()
                except:
                    pass
            self.receive_stream_process = None
            print("Audio receive process stopped")
    
    def _kill_vlc_processes(self) -> None:
        """Kill all VLC processes started by this system."""
        processes = [
            self.personal_mic_stream_process,
            self.global_mic_stream_process,
            self.receive_stream_process
        ]
        
        for process in processes:
            if process:
                try:
                    process.terminate()
                    process.wait(timeout=1)
                except:
                    try:
                        process.kill()
                    except:
                        pass
    
    def _audio_playback_loop(self) -> None:
        """Play received audio with channel muting."""
        print("Starting audio playback loop")
        fifo_path = "/tmp/dans_le_blanc_audio_fifo"
        
        # Make sure FIFO exists
        if not os.path.exists(fifo_path):
            try:
                os.mkfifo(fifo_path)
            except Exception as e:
                print(f"Error creating FIFO: {e}")
                return
        
        # Buffer for audio processing
        buffer_size = 1024 * 2  # 1024 samples * 2 bytes per sample (16-bit)
        
        try:
            while self.running and not self.is_shutting_down:
                try:
                    if not self.playing_audio or self.output_stream is None:
                        time.sleep(0.1)
                        continue
                    
                    # Try to open the FIFO for reading
                    # We use non-blocking open to avoid hanging if there's no data
                    fd = os.open(fifo_path, os.O_RDONLY | os.O_NONBLOCK)
                    try:
                        data = os.read(fd, buffer_size)
                        if data:
                            # Convert bytes to numpy array (assuming stereo int16 format)
                            audio_data = np.frombuffer(data, dtype=np.int16)
                            
                            # Handle odd number of samples
                            if len(audio_data) % 2 != 0:
                                audio_data = audio_data[:-1]
                            
                            # Reshape to stereo (2 channels)
                            if len(audio_data) > 0:
                                try:
                                    audio_data = audio_data.reshape(-1, 2)
                                    
                                    # Apply channel muting
                                    muted_data = audio_data.copy()
                                    
                                    # Mute left channel if needed
                                    if self.muted_channels["left"]:
                                        muted_data[:, 0] = 0
                                    
                                    # Mute right channel if needed
                                    if self.muted_channels["right"]:
                                        muted_data[:, 1] = 0
                                    
                                    # Convert back to bytes
                                    output_data = muted_data.tobytes()
                                    
                                    # Play audio
                                    if self.output_stream and self.output_stream.is_active():
                                        self.output_stream.write(output_data)
                                except Exception as reshape_error:
                                    print(f"Error processing audio data: {reshape_error}")
                    except BlockingIOError:
                        # No data available, sleep briefly
                        time.sleep(0.01)
                    finally:
                        # Close the file descriptor
                        os.close(fd)
                except Exception as e:
                    if self.running and not self.is_shutting_down:
                        print(f"Error in audio playback: {e}")
                    time.sleep(0.1)
        except Exception as e:
            print(f"Error in audio playback loop: {e}")
        finally:
            print("Audio playback thread exiting...")

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
