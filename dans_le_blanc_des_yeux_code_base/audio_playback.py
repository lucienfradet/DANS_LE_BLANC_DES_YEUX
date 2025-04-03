"""
Audio playback module for the Dans le Blanc des Yeux installation.
Handles playing audio with channel muting based on system state.

Playback Logic:
1. When remote device has pressure=true and local doesn't:
   - Play received personal mic with LEFT channel muted

2. When local device has pressure=true and remote doesn't:
   - Play received global mic with RIGHT channel muted

3. When both have pressure=true:
   - Play received personal mic with LEFT channel muted

4. When neither has pressure: No playback
"""

import os
import time
import threading
import queue
import struct
import numpy as np
import pyaudio
import configparser
from collections import deque
from typing import Dict, Optional, Tuple, List, Callable, Any

from system_state import system_state
from audio_streamer import AudioStreamer

# Audio configuration
CHUNK_SIZE = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 2
RATE = 44100
SILENCE_THRESHOLD = 100  # Threshold for audio level to detect silence
BUFFER_SIZE = 10  # Number of audio chunks to buffer

class AudioPlayback:
    """Handles audio playback with channel muting based on system state."""
    
    def __init__(self, audio_streamer: AudioStreamer):
        self.audio_streamer = audio_streamer
        
        # PyAudio instance
        self.p = pyaudio.PyAudio()
        
        # Audio output device ID
        self.speaker_id = None
        
        # Channel muting configuration (will be loaded from config)
        self.muting_config = {
            'global_speaker_mute_channel': 'left',    # Which channel to mute for global speaker
            'personal_speaker_mute_channel': 'right'   # Which channel to mute for personal speaker
        }
        
        # Load settings from config
        self._load_config()
        
        # Audio buffers for received data
        self.global_speaker_buffer = queue.Queue(maxsize=BUFFER_SIZE)
        self.personal_speaker_buffer = queue.Queue(maxsize=BUFFER_SIZE)
        
        # Current playback state
        self.playback_state = "none"  # "none", "global_speaker_left_muted", "personal_speaker_right_muted"
        
        # Threading
        self.running = False
        self.threads = []
        self.lock = threading.Lock()
        
        # Stream for playback
        self.stream = None
        
        # Find audio output device
        self._find_audio_devices()
        
        # Register for audio callbacks
        self.audio_streamer.register_personal_mic_callback(self._on_global_speaker_audio)
        self.audio_streamer.register_global_mic_callback(self._on_personal_speaker_audio)
        
        # Register as observer for state changes
        system_state.add_observer(self._on_state_change)
        
        print("Audio playback initialized")
    
    def _load_config(self):
        """Load audio settings from config.ini"""
        try:
            config = configparser.ConfigParser()
            config.read('config.ini')
            
            if 'audio' in config:
                # Load muting configuration
                self.muting_config['global_speaker_mute_channel'] = config.get('audio', 'global_speaker_mute_channel', fallback='left')
                self.muting_config['personal_speaker_mute_channel'] = config.get('audio', 'personal_speaker_mute_channel', fallback='right')
                
                print(f"Loaded audio muting configuration from config.ini:")
                print(f"  global speaker mute channel: {self.muting_config['global_speaker_mute_channel']}")
                print(f"  personal speaker mute channel: {self.muting_config['personal_speaker_mute_channel']}")
            else:
                print("No [audio] section found in config.ini, using default settings")
                self._add_default_config_settings(config)
                
        except Exception as e:
            print(f"Error loading audio config: {e}")
            print("Using default muting configuration")
    
    def _add_default_config_settings(self, config):
        """Add default audio settings to config.ini if not present"""
        try:
            if 'audio' not in config:
                config['audio'] = {}
            
            # Add muting configuration
            if 'global_speaker_mute_channel' not in config['audio']:
                config['audio']['global_speaker_mute_channel'] = self.muting_config['global_speaker_mute_channel']
            if 'personal_speaker_mute_channel' not in config['audio']:
                config['audio']['personal_speaker_mute_channel'] = self.muting_config['personal_speaker_mute_channel']
            
            # Write to config file
            with open('config.ini', 'w') as configfile:
                config.write(configfile)
                
            print("Added default audio muting settings to config.ini")
        except Exception as e:
            print(f"Error adding default audio settings to config: {e}")
    
    def _find_audio_devices(self) -> None:
        """Find the audio output device by name."""
        # Reset device ID
        self.speaker_id = None
        
        # Get personal mic name from audio_streamer
        personal_mic_name = self.audio_streamer.personal_mic_name
        
        # Find personal output device
        for i in range(self.p.get_device_count()):
            dev_info = self.p.get_device_info_by_index(i)
            
            # Look for personal output
            if personal_mic_name in dev_info["name"] and dev_info["maxOutputChannels"] > 0:
                self.speaker_id = i
                print(f"Found {personal_mic_name} speaker: Device {i}")
                break
        
        # If we didn't find the specific device, use the default output
        if self.speaker_id is None:
            # Try to get the default output device
            try:
                default_info = self.p.get_default_output_device_info()
                self.speaker_id = default_info["index"]
                print(f"Using default output device: {default_info['name']} (Device {self.speaker_id})")
            except Exception as e:
                print(f"Warning: Could not find audio output device: {e}")
                print("Audio playback may not work correctly")
    
    def start(self) -> bool:
        """Start the audio playback system."""
        print("Starting audio playback...")
        self.running = True
        
        # Start playback thread
        playback_thread = threading.Thread(target=self._playback_loop)
        playback_thread.daemon = True
        playback_thread.start()
        self.threads.append(playback_thread)
        
        # Update the playback state based on current system state
        self._update_playback_state()
        
        print("Audio playback started")
        return True
    
    def stop(self) -> None:
        """Stop the audio playback and clean up."""
        print("Stopping audio playback...")
        self.running = False
        
        # Wait for threads to finish
        for thread in self.threads:
            thread.join(timeout=1.0)
        
        # Close the audio stream if it's open
        with self.lock:
            if self.stream is not None:
                self.stream.stop_stream()
                self.stream.close()
                self.stream = None
        
        # Clean up PyAudio
        self.p.terminate()
        
        print("Audio playback stopped")
    
    def _on_state_change(self, changed_state: str) -> None:
        """Handle system state changes."""
        if changed_state in ["local", "remote"]:
            self._update_playback_state()
    
    def _update_playback_state(self) -> None:
        """Update playback state based on the current system state."""
        local_state = system_state.get_local_state()
        remote_state = system_state.get_remote_state()
        
        old_state = self.playback_state
        
        # Only proceed if remote is connected
        if not remote_state.get("connected", False):
            self.playback_state = "none"
        
        # Case 1: Both have pressure - play global speaker with configured channel muted
        elif local_state.get("pressure", False) and remote_state.get("pressure", False):
            if self.muting_config['global_speaker_mute_channel'] == 'left':
                self.playback_state = "global_speaker_left_muted"
            else:
                self.playback_state = "global_speaker_right_muted"
        
        # Case 2: Remote has pressure but local doesn't - play global speaker with configured channel muted
        elif remote_state.get("pressure", False) and not local_state.get("pressure", False):
            if self.muting_config['global_speaker_mute_channel'] == 'left':
                self.playback_state = "global_speaker_left_muted"
            else:
                self.playback_state = "global_speaker_right_muted"
        
        # Case 3: Local has pressure but remote doesn't - play personal speaker with configured channel muted
        elif local_state.get("pressure", False) and not remote_state.get("pressure", False):
            if self.muting_config['personal_speaker_mute_channel'] == 'left':
                self.playback_state = "personal_speaker_left_muted"
            else:
                self.playback_state = "personal_speaker_right_muted"
        
        # Case 4: No pressure on either - no playback
        else:
            self.playback_state = "none"
        
        if old_state != self.playback_state:
            print(f"Playback state changed from {old_state} to {self.playback_state}")
            
            # Clear buffers when changing state
            self._clear_buffers()
    
    def _clear_buffers(self) -> None:
        """Clear audio buffers."""
        with self.lock:
            # Clear global speaker buffer
            while not self.global_speaker_buffer.empty():
                try:
                    self.global_speaker_buffer.get_nowait()
                except queue.Empty:
                    break
            
            # Clear personal speaker buffer
            while not self.personal_speaker_buffer.empty():
                try:
                    self.personal_speaker_buffer.get_nowait()
                except queue.Empty:
                    break
    
    def _on_global_speaker_audio(self, data: bytes) -> None:
        """Handle received personal mic audio."""
        try:
            # Only buffer if we're playing this audio 
            if "global_speaker" in self.playback_state:
                # If buffer is full, remove oldest chunk
                if self.global_speaker_buffer.full():
                    try:
                        self.global_speaker_buffer.get_nowait()
                    except queue.Empty:
                        pass
                
                # Add new data to buffer
                self.global_speaker_buffer.put_nowait(data)
        except Exception as e:
            print(f"Error handling personal mic audio: {e}")
    
    def _on_personal_speaker_audio(self, data: bytes) -> None:
        """Handle received USB Audio Device mic audio."""
        try:
            # Only buffer if we're playing this audio
            if "personal_speaker" in self.playback_state:
                # If buffer is full, remove oldest chunk
                if self.personal_speaker_buffer.full():
                    try:
                        self.personal_speaker_buffer.get_nowait()
                    except queue.Empty:
                        pass
                
                # Add new data to buffer
                self.personal_speaker_buffer.put_nowait(data)
        except Exception as e:
            print(f"Error handling USB Audio Device mic audio: {e}")
    
    def _apply_channel_muting(self, audio_data: bytes, mute_left: bool = False, mute_right: bool = False) -> bytes:
        """
        Apply channel muting to audio data.
        
        Args:
            audio_data: Raw audio data bytes
            mute_left: Whether to mute the left channel
            mute_right: Whether to mute the right channel
                
        Returns:
            Audio data with channel muting applied
        """
        try:
            # Skip if no muting needed
            if not mute_left and not mute_right:
                return audio_data
            
            # Convert bytes to numpy array
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            
            # Create a copy to ensure we have a writeable array
            audio_array = audio_array.copy()
            
            # Reshape to separate channels (assuming stereo)
            audio_array = audio_array.reshape(-1, 2)
            
            # Apply muting
            if mute_left:
                audio_array[:, 0] = 0
            if mute_right:
                audio_array[:, 1] = 0
            
            # Convert back to bytes
            return audio_array.tobytes()
        except Exception as e:
            print(f"Error applying channel muting: {e}")
            return audio_data  # Return original data on error
    
    def _callback(self, in_data, frame_count, time_info, status):
        """
        PyAudio callback for audio playback.
        
        This is called by PyAudio when it needs more audio data to play.
        We provide the properly muted audio data based on current state.
        """
        # Get the current playback state
        state = self.playback_state
        
        # Default to silence if we have no data
        output_data = np.zeros(frame_count * CHANNELS, dtype=np.int16).tobytes()
        
        try:
            if state == "global_speaker_left_muted":
                # Play global speaker with LEFT channel muted
                if not self.global_speaker_buffer.empty():
                    data = self.global_speaker_buffer.get_nowait()
                    output_data = self._apply_channel_muting(data, mute_left=True, mute_right=False)
            
            elif state == "global_speaker_right_muted":
                # Play global speaker with RIGHT channel muted
                if not self.global_speaker_buffer.empty():
                    data = self.global_speaker_buffer.get_nowait()
                    output_data = self._apply_channel_muting(data, mute_left=False, mute_right=True)
                
            elif state == "personal_speaker_left_muted":
                # Play personal speaker with LEFT channel muted
                if not self.personal_speaker_buffer.empty():
                    data = self.personal_speaker_buffer.get_nowait()
                    output_data = self._apply_channel_muting(data, mute_left=True, mute_right=False)
                
            elif state == "personal_speaker_right_muted":
                # Play personal speaker with RIGHT channel muted
                if not self.personal_speaker_buffer.empty():
                    data = self.personal_speaker_buffer.get_nowait()
                    output_data = self._apply_channel_muting(data, mute_left=False, mute_right=True)
                
            elif state == "none":
                # Play silence
                pass
        except Exception as e:
            print(f"Error in audio callback: {e}")
        
        return (output_data, pyaudio.paContinue)
    
    def _playback_loop(self) -> None:
        """Main playback loop."""
        print("Playback loop started")
        
        # Only proceed if we have an output device
        if self.speaker_id is None:
            print("No audio output device available")
            return
        
        try:
            # Create a PyAudio stream for playback
            with self.lock:
                self.stream = self.p.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    output=True,
                    output_device_index=self.speaker_id,
                    frames_per_buffer=CHUNK_SIZE,
                    stream_callback=self._callback
                )
                
                # Start the stream
                self.stream.start_stream()
            
            print(f"Audio playback started on device {self.speaker_id}")
            
            # Keep the thread alive while the stream is active
            while self.running and self.stream.is_active():
                time.sleep(0.1)
                
        except Exception as e:
            print(f"Error in playback loop: {e}")
        finally:
            with self.lock:
                if self.stream is not None:
                    self.stream.stop_stream()
                    self.stream.close()
                    self.stream = None
            
            print("Playback loop stopped")


# Test function for the audio playback
def test_audio_playback():
    """Test the audio playback system."""
    from audio_streamer import AudioStreamer
    
    # Set up system state for testing
    system_state.update_local_state({"pressure": False})
    system_state.update_remote_state({"pressure": True, "connected": True})
    
    # Initialize audio streamer
    audio_streamer = AudioStreamer("127.0.0.1")
    audio_streamer.start()
    
    # Initialize audio playback
    audio_playback = AudioPlayback(audio_streamer)
    audio_playback.start()
    
    try:
        print("\nTesting different pressure states:")
        print("\n1. Remote pressure, local no pressure - global mic LEFT muted")
        time.sleep(5)
        
        print("\n2. Both have pressure - global mic LEFT muted")
        system_state.update_local_state({"pressure": True})
        time.sleep(5)
        
        print("\n3. Local pressure, remote no pressure - personal mic RIGHT muted")
        system_state.update_remote_state({"pressure": False})
        time.sleep(5)
        
        print("\n4. Neither has pressure - No playback")
        system_state.update_local_state({"pressure": False})
        time.sleep(5)
        
        print("\nAudio playback test complete.")
        
    except KeyboardInterrupt:
        print("Test interrupted by user")
    finally:
        audio_playback.stop()
        audio_streamer.stop()


# Run test if executed directly
if __name__ == "__main__":
    test_audio_playback()
