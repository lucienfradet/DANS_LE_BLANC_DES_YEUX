"""
Audio streaming system for the Dans le Blanc des Yeux installation.
Handles audio capture and playback between Pi devices based on pressure states.

Logic:
1. When both have pressure: Both send their mic outputs to the other device and play it.
2. When remote has pressure and local doesn't: Device sends its mic sound and plays nothing.
3. When local has pressure and remote doesn't: Device plays the mic and sends nothing.
4. When neither has pressure: No playback or streaming.
"""

import pyaudio
import socket
import threading
import configparser
import time
import struct
import numpy as np
from typing import Dict, Optional, Tuple, List, Callable

from system_state import system_state

# Default audio configuration
DEFAULT_FORMAT = pyaudio.paInt16
DEFAULT_CHANNELS = 1
DEFAULT_RATE = 44100
DEFAULT_CHUNK = 1024
DEFAULT_PORT = 5002  # Port for audio streaming

class AudioSystem:
    """Handles audio capture and playback between devices based on pressure states."""
    
    def __init__(self, remote_ip: str):
        self.remote_ip = remote_ip
        
        # PyAudio instance
        self.p = None
        
        # Streams
        self.input_stream = None
        self.output_stream = None
        
        # Audio format settings
        self.format = DEFAULT_FORMAT
        self.channels = DEFAULT_CHANNELS
        self.rate = DEFAULT_RATE
        self.chunk = DEFAULT_CHUNK
        
        # Device IDs
        self.input_device_id = None
        self.output_device_id = None
        
        # Stream state
        self.sending_audio = False
        self.receiving_audio = False
        self.muted = True
        
        # Socket for streaming
        self.send_socket = None
        self.receive_socket = None
        
        # Threading
        self.running = False
        self.threads = []
        self.lock = threading.Lock()
        
        # Load configuration
        self.config = configparser.ConfigParser()
        self.config.read('config.ini')
        self.mic_name = self._get_config_value('audio', 'global_mic_name', 'USB Audio Device')
        
        # Register as observer for state changes
        system_state.add_observer(self._on_state_change)
        
        # Audio buffer for smoother playback
        self.audio_buffer = []
        self.buffer_lock = threading.Lock()
        self.buffer_size = 5  # Number of chunks to buffer
        
        print(f"Audio system initialized with remote IP: {remote_ip}")
    
    def start(self) -> bool:
        """Start the audio system."""
        print("Starting audio system...")
        self.running = True
        
        # Initialize PyAudio
        try:
            self.p = pyaudio.PyAudio()
            
            # Print audio device info for debugging
            self._print_audio_device_info()
            
            # Find input and output devices
            self.input_device_id = self._find_input_device()
            self.output_device_id = self._find_output_device()
            
            if self.input_device_id is None:
                print(f"Could not find mic with name '{self.mic_name}'")
                print("Available input devices:")
                self._print_audio_device_info(input_only=True)
                print("\nTIP: You can manually specify input_device_index in config.ini:")
                print("[audio]")
                print("input_device_index=2  # Replace with the device ID for your USB Audio Device")
                return False
            
            if self.output_device_id is None:
                print("Could not find audio output device")
                print("Available output devices:")
                self._print_audio_device_info(output_only=True)
                print("\nTIP: You can manually specify output_device_index in config.ini:")
                print("[audio]")
                print("output_device_index=0  # Replace with the device ID for your TX 96Khz device")
                return False
            
            # Print details of the selected devices
            if self.input_device_id is not None:
                input_device_info = self.p.get_device_info_by_index(self.input_device_id)
                print(f"Selected input device {self.input_device_id}: {input_device_info.get('name', 'Unknown')}")
                
            if self.output_device_id is not None:
                output_device_info = self.p.get_device_info_by_index(self.output_device_id)
                print(f"Selected output device {self.output_device_id}: {output_device_info.get('name', 'Unknown')}")
            
            # Start audio streams
            if not self._start_audio_streams():
                print("Failed to start audio streams")
                return False
            
            # Initialize sockets
            self._init_sockets()
            
            # Start receiver thread
            receiver_thread = threading.Thread(target=self._receiver_loop)
            receiver_thread.daemon = True
            receiver_thread.start()
            self.threads.append(receiver_thread)
            
            # Start sender thread
            sender_thread = threading.Thread(target=self._sender_loop)
            sender_thread.daemon = True
            sender_thread.start()
            self.threads.append(sender_thread)
            
            # Check initial state to see if we need to start streaming right away
            self._update_audio_based_on_state()
            
            print("Audio system started successfully")
            return True
        except Exception as e:
            print(f"Error starting audio system: {e}")
            import traceback
            traceback.print_exc()
            self.running = False
            return False
    
    def stop(self) -> None:
        """Stop the audio system and release resources."""
        print("Stopping audio system...")
        self.running = False
        
        # Wait for threads to finish
        for thread in self.threads:
            thread.join(timeout=1.0)
        
        # Stop and close audio streams
        with self.lock:
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
            
            if self.p:
                self.p.terminate()
                self.p = None
        
        # Close sockets
        if self.send_socket:
            self.send_socket.close()
            self.send_socket = None
        
        if self.receive_socket:
            self.receive_socket.close()
            self.receive_socket = None
        
        print("Audio system stopped")
    
    def _get_config_value(self, section: str, key: str, default_value: str) -> str:
        """Get a value from the config file with a default fallback."""
        if section in self.config and key in self.config[section]:
            return self.config[section][key]
        return default_value
    
    def _print_audio_device_info(self, input_only: bool = False, output_only: bool = False) -> None:
        """Print available audio devices for debugging."""
        if not self.p:
            return
            
        print("\n--- PyAudio Devices ---")
        for i in range(self.p.get_device_count()):
            try:
                dev_info = self.p.get_device_info_by_index(i)
                
                # Skip if we're only looking for specific types
                if input_only and dev_info.get('maxInputChannels', 0) <= 0:
                    continue
                if output_only and dev_info.get('maxOutputChannels', 0) <= 0:
                    continue
                
                print(f"Device {i}: {dev_info.get('name', 'Unknown')}")
                print(f"  Input channels: {dev_info.get('maxInputChannels', 0)}")
                print(f"  Output channels: {dev_info.get('maxOutputChannels', 0)}")
                print(f"  Default sample rate: {dev_info.get('defaultSampleRate', 0)}")
                if 'hostApi' in dev_info:
                    host_api_info = self.p.get_host_api_info_by_index(dev_info['hostApi'])
                    print(f"  Host API: {host_api_info.get('name', 'Unknown')}")
                
                # Highlight the mic we're looking for
                if self.mic_name.lower() in dev_info.get('name', '').lower():
                    print(f"  --> MATCHES CONFIGURED MIC NAME '{self.mic_name}'")
            except Exception as e:
                print(f"Error getting device info for device {i}: {e}")
        print("--- End PyAudio Devices ---\n")
        
        # Also print ALSA device info for better debugging
        try:
            self._print_audio_device_info_alsa()
        except Exception as e:
            print(f"Error printing ALSA device info: {e}")
    
    def _find_input_device(self) -> Optional[int]:
        """Find the ID of the specified input device (mic)."""
        if not self.p:
            return None
            
        # Check if we have a specific device index in config
        try:
            if 'audio' in self.config and 'input_device_index' in self.config['audio']:
                idx = int(self.config['audio']['input_device_index'])
                dev_info = self.p.get_device_info_by_index(idx)
                if dev_info.get('maxInputChannels', 0) > 0:
                    print(f"Using configured input device index {idx}: {dev_info.get('name', 'Unknown')}")
                    return idx
        except Exception as e:
            print(f"Error using configured input device index: {e}")
                
        # Try to find a device with any of these potential matches
        search_terms = [
            self.mic_name.lower(),
            "usb audio device",
            "audio device",
            "usb audio"
        ]
        
        # First full match pass
        for i in range(self.p.get_device_count()):
            try:
                dev_info = self.p.get_device_info_by_index(i)
                if dev_info.get('maxInputChannels', 0) > 0:
                    dev_name = dev_info.get('name', '').lower()
                    for term in search_terms:
                        if term in dev_name:
                            print(f"Found input device: {dev_info.get('name', 'Unknown')}")
                            return i
            except Exception:
                continue
        
        # Try to find a USB audio input device
        for i in range(self.p.get_device_count()):
            try:
                dev_info = self.p.get_device_info_by_index(i)
                if (dev_info.get('maxInputChannels', 0) > 0 and 
                    "usb" in dev_info.get('name', '').lower() and
                    "audio" in dev_info.get('name', '').lower()):
                    print(f"Found USB audio input device: {dev_info.get('name', 'Unknown')}")
                    return i
            except Exception:
                continue
                
        # If not found, try to find any hardware input device
        for i in range(self.p.get_device_count()):
            try:
                dev_info = self.p.get_device_info_by_index(i)
                if (dev_info.get('maxInputChannels', 0) > 0 and
                    "hw:" in dev_info.get('name', '').lower()):
                    print(f"Found hardware input device: {dev_info.get('name', 'Unknown')}")
                    return i
            except Exception:
                continue
        
        # If still not found, try to use the default input device
        try:
            default_input = self.p.get_default_input_device_info()
            print(f"Using default input device: {default_input.get('name', 'Unknown')}")
            return default_input.get('index', None)
        except Exception:
            return None
    
    def _find_output_device(self) -> Optional[int]:
        """Find the ID of a suitable output device."""
        if not self.p:
            return None
            
        # Check if we have a specific device index in config
        try:
            if 'audio' in self.config and 'output_device_index' in self.config['audio']:
                idx = int(self.config['audio']['output_device_index'])
                dev_info = self.p.get_device_info_by_index(idx)
                if dev_info.get('maxOutputChannels', 0) > 0:
                    print(f"Using configured output device index {idx}: {dev_info.get('name', 'Unknown')}")
                    return idx
        except Exception as e:
            print(f"Error using configured output device index: {e}")
            
        # First try to find the TX 96Khz device mentioned in requirements
        for i in range(self.p.get_device_count()):
            try:
                dev_info = self.p.get_device_info_by_index(i)
                dev_name = dev_info.get('name', '').lower()
                if dev_info.get('maxOutputChannels', 0) > 0:
                    # Try different variations of the name that might appear
                    if ("tx" in dev_name and "96" in dev_name) or \
                       ("96khz" in dev_name) or \
                       ("tx 96khz" in dev_name):
                        print(f"Found TX 96Khz output device: {dev_info.get('name', 'Unknown')}")
                        return i
            except Exception:
                continue
        
        # If TX 96Khz not found, try hardware output devices
        for i in range(self.p.get_device_count()):
            try:
                dev_info = self.p.get_device_info_by_index(i)
                if (dev_info.get('maxOutputChannels', 0) > 0 and
                    "hw:" in dev_info.get('name', '').lower()):
                    print(f"Found hardware output device: {dev_info.get('name', 'Unknown')}")
                    return i
            except Exception:
                continue
                
        # Try any output device
        for i in range(self.p.get_device_count()):
            try:
                dev_info = self.p.get_device_info_by_index(i)
                if dev_info.get('maxOutputChannels', 0) > 0:
                    print(f"Found output device: {dev_info.get('name', 'Unknown')}")
                    return i
            except Exception:
                continue
        
        # If still not found, try to use the default output device
        try:
            default_output = self.p.get_default_output_device_info()
            print(f"Using default output device: {default_output.get('name', 'Unknown')}")
            return default_output.get('index', None)
        except Exception:
            return None
    
    def _start_audio_streams(self) -> bool:
        """Initialize and start audio input and output streams."""
        if not self.p:
            return False
            
        try:
            # Start input stream
            self.input_stream = self.p.open(
                format=self.format,
                channels=self.channels,
                rate=self.rate,
                input=True,
                input_device_index=self.input_device_id,
                frames_per_buffer=self.chunk,
                stream_callback=self._input_callback
            )
            
            # Start output stream
            self.output_stream = self.p.open(
                format=self.format,
                channels=self.channels,
                rate=self.rate,
                output=True,
                output_device_index=self.output_device_id,
                frames_per_buffer=self.chunk,
                stream_callback=self._output_callback
            )
            
            # Initially mute the output
            self.muted = True
            
            return True
        except Exception as e:
            print(f"Error starting audio streams: {e}")
            return False
    
    def _input_callback(self, in_data, frame_count, time_info, status):
        """Callback for input stream - handles capturing audio."""
        # We don't process the data here, just return it
        # The sender thread will handle sending it if needed
        return (in_data, pyaudio.paContinue)
    
    def _output_callback(self, in_data, frame_count, time_info, status):
        """Callback for output stream - handles playing audio."""
        if self.muted:
            # If muted, return silence
            return (b'\x00' * self.chunk * self.channels * 2, pyaudio.paContinue)
        
        # If we have data in the buffer, play it
        with self.buffer_lock:
            if self.audio_buffer:
                data = self.audio_buffer.pop(0)
                return (data, pyaudio.paContinue)
        
        # If no data, play silence
        return (b'\x00' * self.chunk * self.channels * 2, pyaudio.paContinue)
    
    def _init_sockets(self) -> None:
        """Initialize sockets for sending and receiving audio."""
        # Socket for sending audio data
        self.send_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        # Socket for receiving audio data
        self.receive_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.receive_socket.bind(("0.0.0.0", DEFAULT_PORT))
        self.receive_socket.settimeout(0.5)  # Set timeout for responsive shutdown
    
    def _on_state_change(self, changed_state: str) -> None:
        """Handle system state changes."""
        if changed_state in ["local", "remote"]:
            self._update_audio_based_on_state()
    
    def _update_audio_based_on_state(self) -> None:
        """Update audio streaming and playback based on pressure states."""
        local_state = system_state.get_local_state()
        remote_state = system_state.get_remote_state()
        
        # Only proceed if remote is connected
        if not remote_state.get("connected", False):
            self._stop_all_audio()
            return
        
        # Get pressure states
        local_pressure = local_state.get("pressure", False)
        remote_pressure = remote_state.get("pressure", False)
        
        # Case 1: Both have pressure - both send mic and play audio
        if local_pressure and remote_pressure:
            self._start_sending_audio()
            self._start_receiving_audio()
            print("Audio state: Both sending and receiving audio")
            
            # Update audio state in system_state
            system_state.update_audio_state({
                "playing": True,
                "streaming_mic": self.mic_name,
                "muted_channels": []
            })
        
        # Case 2: Remote has pressure, local doesn't - send mic but don't play
        elif remote_pressure and not local_pressure:
            self._start_sending_audio()
            self._stop_receiving_audio()
            print("Audio state: Sending audio only")
            
            # Update audio state in system_state
            system_state.update_audio_state({
                "playing": False,
                "streaming_mic": self.mic_name,
                "muted_channels": ["output"]
            })
        
        # Case 3: Local has pressure, remote doesn't - play received audio but don't send
        elif local_pressure and not remote_pressure:
            self._stop_sending_audio()
            self._start_receiving_audio()
            print("Audio state: Receiving audio only")
            
            # Update audio state in system_state
            system_state.update_audio_state({
                "playing": True,
                "streaming_mic": "none",
                "muted_channels": ["input"]
            })
        
        # Case 4: Neither has pressure - no playback or streaming
        else:
            self._stop_all_audio()
            print("Audio state: No audio streaming or playback")
            
            # Update audio state in system_state
            system_state.update_audio_state({
                "playing": False,
                "streaming_mic": "none",
                "muted_channels": ["input", "output"]
            })
    
    def _start_sending_audio(self) -> None:
        """Start sending audio data from the microphone."""
        if not self.sending_audio:
            self.sending_audio = True
            print(f"Started sending audio to {self.remote_ip}:{DEFAULT_PORT}")
    
    def _stop_sending_audio(self) -> None:
        """Stop sending audio data."""
        if self.sending_audio:
            self.sending_audio = False
            print("Stopped sending audio")
    
    def _start_receiving_audio(self) -> None:
        """Start receiving and playing audio data."""
        with self.lock:
            self.receiving_audio = True
            self.muted = False
            
            # Clear buffer
            with self.buffer_lock:
                self.audio_buffer.clear()
                
        print("Started receiving and playing audio")
    
    def _stop_receiving_audio(self) -> None:
        """Stop receiving and playing audio data."""
        with self.lock:
            self.receiving_audio = False
            self.muted = True
            
            # Clear buffer
            with self.buffer_lock:
                self.audio_buffer.clear()
                
        print("Stopped receiving and playing audio")
    
    def _stop_all_audio(self) -> None:
        """Stop all audio operations."""
        self._stop_sending_audio()
        self._stop_receiving_audio()
    
    def _sender_loop(self) -> None:
        """Thread that sends audio data when appropriate."""
        print("Audio sender thread started")
        
        packet_counter = 0
        last_report_time = time.time()
        
        while self.running:
            try:
                if self.sending_audio and self.input_stream:
                    # Read data from input stream
                    data = self.input_stream.read(self.chunk, exception_on_overflow=False)
                    
                    if data:
                        # Send data to remote
                        self.send_socket.sendto(data, (self.remote_ip, DEFAULT_PORT))
                        
                        # Log status periodically
                        packet_counter += 1
                        current_time = time.time()
                        if current_time - last_report_time > 10:
                            print(f"Sent {packet_counter} audio packets")
                            packet_counter = 0
                            last_report_time = current_time
                else:
                    # Not sending, sleep to avoid busy loop
                    time.sleep(0.1)
            except Exception as e:
                print(f"Error in audio sender: {e}")
                time.sleep(1.0)
    
    def _receiver_loop(self) -> None:
        """Thread that receives audio data when appropriate."""
        print("Audio receiver thread started")
        
        packet_counter = 0
        last_report_time = time.time()
        
        while self.running:
            try:
                if self.receiving_audio:
                    try:
                        # Receive data with timeout
                        data, addr = self.receive_socket.recvfrom(65536)
                        
                        if data:
                            # Add to buffer for playback
                            with self.buffer_lock:
                                # Keep buffer from growing too large
                                if len(self.audio_buffer) < self.buffer_size:
                                    self.audio_buffer.append(data)
                            
                            # Log status periodically
                            packet_counter += 1
                            current_time = time.time()
                            if current_time - last_report_time > 10:
                                print(f"Received {packet_counter} audio packets")
                                packet_counter = 0
                                last_report_time = current_time
                    except socket.timeout:
                        # Expected due to socket timeout
                        pass
                else:
                    # Not receiving, sleep to avoid busy loop
                    time.sleep(0.1)
            except Exception as e:
                print(f"Error in audio receiver: {e}")
                time.sleep(1.0)


# Function to run the audio system standalone for testing
def test_audio_system(remote_ip="127.0.0.1"):
    """Test the audio system with different pressure states."""
    # Set up system state for testing
    system_state.update_local_state({"pressure": False})
    system_state.update_remote_state({"pressure": True, "connected": True})
    
    # Initialize audio system
    audio_system = AudioSystem(remote_ip)
    if not audio_system.start():
        print("Failed to start audio system")
        return
    
    try:
        print("\nTesting different pressure states:")
        print("\n1. Remote pressure, local no pressure - Should send mic but not play")
        time.sleep(5)
        
        print("\n2. Both have pressure - Should send mic and play")
        system_state.update_local_state({"pressure": True})
        time.sleep(5)
        
        print("\n3. Local pressure, remote no pressure - Should play but not send")
        system_state.update_remote_state({"pressure": False})
        time.sleep(5)
        
        print("\n4. Neither has pressure - Should do nothing")
        system_state.update_local_state({"pressure": False})
        time.sleep(5)
        
        print("\nAudio test complete. Press Ctrl+C to exit.")
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("Test interrupted by user")
    finally:
        audio_system.stop()


# Run test if executed directly
if __name__ == "__main__":
    test_audio_system()
