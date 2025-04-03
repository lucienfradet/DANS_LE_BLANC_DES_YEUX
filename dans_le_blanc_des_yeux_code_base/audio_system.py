"""
Audio system for Dans le Blanc des Yeux installation using VLC subprocesses.
Handles audio streaming between devices and channel muting based on pressure states.
"""

import subprocess
import time
import threading
import socket
import os
import signal
import configparser
from typing import Dict, Any, Optional, List, Tuple

from system_state import system_state

# Default streaming parameters
HTTP_STREAM_PORT = 8888
HTTP_AUDIO_PATH = "audio.mp3"
MP3_BITRATE = 64  # in kbps
AUDIO_VOLUME = 80  # Default volume level (0-100)

class AudioSystem:
    """Handles audio capture, playback, and streaming using VLC subprocesses."""
    
    def __init__(self, remote_ip: str):
        self.remote_ip = remote_ip
        
        # Streaming URLs
        self.local_stream_url = f"http://0.0.0.0:{HTTP_STREAM_PORT}/{HTTP_AUDIO_PATH}"
        self.remote_stream_url = f"http://{remote_ip}:{HTTP_STREAM_PORT}/{HTTP_AUDIO_PATH}"
        
        # Audio device configuration
        self.personal_mic_name = "TX 96Khz"
        self.global_mic_name = "USB Audio Device"
        self.personal_mic_device = None
        self.global_mic_device = None
        self.output_device = None
        
        # Channel muting configuration
        self.global_speaker_mute_channel = "right"
        self.personal_speaker_mute_channel = "left"
        
        # Process tracking
        self.streaming_process = None
        self.playback_process = None
        
        # State tracking
        self.streaming_mic = None  # 'personal', 'global', or None
        self.playing_audio = False
        self.muted_channels = {"left": False, "right": False}
        
        # Thread control
        self.running = False
        self.lock = threading.Lock()
        self.monitor_thread = None
        
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
                
                # Load device names
                self.personal_mic_name = config.get('audio', 'personal_mic_name', fallback='TX 96Khz')
                self.global_mic_name = config.get('audio', 'global_mic_name', fallback='USB Audio Device')
                
                print(f"Audio configuration loaded:")
                print(f"  Global speaker mute channel: {self.global_speaker_mute_channel}")
                print(f"  Personal speaker mute channel: {self.personal_speaker_mute_channel}")
                print(f"  Personal mic name: {self.personal_mic_name}")
                print(f"  Global mic name: {self.global_mic_name}")
            else:
                print("No [audio] section found in config.ini, using default settings")
        except Exception as e:
            print(f"Error loading audio config: {e}")
            print("Using default audio settings")
    
    def _find_audio_devices(self):
        """Find ALSA device IDs for audio devices based on names."""
        try:
            # Get list of ALSA devices using arecord command
            arecord_output = subprocess.check_output(['arecord', '-l'], text=True)
            aplay_output = subprocess.check_output(['aplay', '-l'], text=True)
            
            print("\nAvailable audio input devices:")
            print(arecord_output)
            
            print("\nAvailable audio output devices:")
            print(aplay_output)
            
            # Find personal mic (TX 96Khz)
            self.personal_mic_device = self._find_alsa_device(arecord_output, self.personal_mic_name)
            if self.personal_mic_device:
                print(f"Found personal mic: {self.personal_mic_name} -> {self.personal_mic_device}")
            else:
                print(f"Warning: Could not find personal mic with name '{self.personal_mic_name}'")
                print("Falling back to default input device")
                self.personal_mic_device = "default"
            
            # Find global mic (USB Audio Device)
            self.global_mic_device = self._find_alsa_device(arecord_output, self.global_mic_name)
            if self.global_mic_device:
                print(f"Found global mic: {self.global_mic_name} -> {self.global_mic_device}")
            else:
                print(f"Warning: Could not find global mic with name '{self.global_mic_name}'")
                if self.personal_mic_device and self.personal_mic_device != "default":
                    print(f"Using personal mic device for global mic as well")
                    self.global_mic_device = self.personal_mic_device
                else:
                    print("Falling back to default input device")
                    self.global_mic_device = "default"
            
            # Find output device
            self.output_device = "default"  # Default ALSA output device
            print(f"Using default ALSA output device")
            
            return True
        except Exception as e:
            print(f"Error finding audio devices: {e}")
            print("Using default audio devices")
            self.personal_mic_device = "default"
            self.global_mic_device = "default"
            self.output_device = "default"
            return False
    
    def _find_alsa_device(self, arecord_output, device_name):
        """Parse arecord -l output to find ALSA device by name."""
        device_name = device_name.lower()
        lines = arecord_output.split('\n')
        
        for line in lines:
            if 'card' in line.lower() and device_name in line.lower():
                # Extract card and device numbers
                try:
                    card_match = line.split('card ')[1].split(':')[0].strip()
                    device_match = line.split('device ')[1].split(':')[0].strip()
                    return f"hw:{card_match},{device_match}"
                except:
                    continue
        
        return None
    
    def start(self) -> bool:
        """Start the audio system."""
        print("Starting audio system...")
        
        try:
            # Check if VLC is installed
            try:
                subprocess.check_output(['cvlc', '--version'], stderr=subprocess.STDOUT)
                print("Found VLC command-line tool")
            except (subprocess.SubprocessError, FileNotFoundError):
                print("Error: VLC is not installed or not in PATH")
                print("Please install VLC: sudo apt-get install vlc")
                return False
            
            # Find audio devices
            self._find_audio_devices()
            
            # Start monitoring thread
            self.running = True
            self.monitor_thread = threading.Thread(target=self._monitor_loop)
            self.monitor_thread.daemon = True
            self.monitor_thread.start()
            
            # Check initial state to see if we need to start streaming right away
            self._update_audio_based_on_state()
            
            print("Audio system started successfully")
            return True
        except Exception as e:
            print(f"Error starting audio system: {e}")
            self.stop()
            return False
    
    def stop(self) -> None:
        """Stop the audio system and release resources."""
        print("Stopping audio system...")
        
        # Signal monitoring thread to stop
        self.running = False
        
        # Wait for thread to finish
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2.0)
        
        # Stop all processes
        self._stop_streaming()
        self._stop_playback()
        
        print("Audio system stopped")
    
    def _monitor_loop(self) -> None:
        """Monitor VLC processes and restart if necessary."""
        print("Audio monitoring thread started")
        
        while self.running:
            try:
                # Check if streaming process is running when it should be
                if self.streaming_mic and self.streaming_process:
                    # Check if process has terminated unexpectedly
                    if self.streaming_process.poll() is not None:
                        print(f"Streaming process terminated unexpectedly, restarting...")
                        self._start_streaming(self.streaming_mic)
                
                # Check if playback process is running when it should be
                if self.playing_audio and self.playback_process:
                    # Check if process has terminated unexpectedly
                    if self.playback_process.poll() is not None:
                        print(f"Playback process terminated unexpectedly, restarting...")
                        self._start_playback()
                
                # Sleep before next check
                time.sleep(5)
            except Exception as e:
                print(f"Error in monitoring thread: {e}")
                time.sleep(10)
    
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
            
            # Stream personal mic to remote
            self._start_streaming("personal")
            self._start_playback()
            
            # Update system state
            system_state.update_local_state({"audio": {
                "playing": True,
                "muted_channels": ["left"],
                "streaming_mic": "personal"
            }})
            print("Audio mode: Both have pressure - LEFT muted, streaming personal mic")
        
        # Case 2: Remote has pressure and local doesn't
        elif remote_state.get("pressure", False) and not local_state.get("pressure", False):
            # Play with RIGHT channel muted
            self._set_muted_channels(left=False, right=True)
            
            # Stream global mic to remote
            self._start_streaming("global")
            self._start_playback()
            
            # Update system state
            system_state.update_local_state({"audio": {
                "playing": True,
                "muted_channels": ["right"],
                "streaming_mic": "global"
            }})
            print("Audio mode: Remote pressure only - RIGHT muted, streaming global mic")
        
        # Case 3: Local has pressure and remote doesn't
        elif local_state.get("pressure", False) and not remote_state.get("pressure", False):
            # Play with LEFT channel muted
            self._set_muted_channels(left=True, right=False)
            
            # Stream personal mic to remote
            self._start_streaming("personal")
            self._start_playback()
            
            # Update system state
            system_state.update_local_state({"audio": {
                "playing": True,
                "muted_channels": ["left"],
                "streaming_mic": "personal"
            }})
            print("Audio mode: Local pressure only - LEFT muted, streaming personal mic")
        
        # Case 4: No pressure on either device
        else:
            # No playback
            self._stop_all_audio()
            print("Audio mode: No pressure - audio stopped")
    
    def _stop_all_audio(self) -> None:
        """Stop all audio streaming and playback."""
        self._stop_streaming()
        self._stop_playback()
        
        # Update system state
        system_state.update_local_state({"audio": {
            "playing": False,
            "muted_channels": ["left", "right"],
            "streaming_mic": "none"
        }})
    
    def _set_muted_channels(self, left: bool, right: bool) -> None:
        """Set the muting state for left and right channels."""
        with self.lock:
            # Store current muting state
            self.muted_channels["left"] = left
            self.muted_channels["right"] = right
            
            # If we're already playing audio, apply the changes
            if self.playing_audio:
                self._restart_playback_with_muting()
    
    def _start_streaming(self, mic_type: str) -> bool:
        """Start streaming audio from specified microphone.
        
        Args:
            mic_type: Either 'personal' or 'global'
        
        Returns:
            True if streaming started successfully, False otherwise
        """
        # First, stop any existing streaming
        self._stop_streaming()
        
        try:
            # Select device based on mic type
            device = self.personal_mic_device if mic_type == "personal" else self.global_mic_device
            
            # Build VLC command
            cmd = [
                'cvlc',
                f'alsa://{device}',
                '--sout', f'#transcode{{acodec=mp3,ab={MP3_BITRATE},channels=2}}:http{{mux=mp3,dst=0.0.0.0:{HTTP_STREAM_PORT}/{HTTP_AUDIO_PATH}}}',
                '--no-sout-all',
                '--sout-keep',
                '--quiet'
            ]
            
            print(f"Starting {mic_type} mic streaming: {' '.join(cmd)}")
            
            # Start VLC process
            self.streaming_process = subprocess.Popen(
                cmd, 
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # Update state
            self.streaming_mic = mic_type
            
            # Wait a moment for streaming to start
            time.sleep(1)
            
            return True
        except Exception as e:
            print(f"Error starting audio streaming: {e}")
            self.streaming_mic = None
            return False
    
    def _stop_streaming(self) -> None:
        """Stop any active audio streaming."""
        if self.streaming_process:
            try:
                # Send SIGTERM to VLC process
                self.streaming_process.terminate()
                
                # Wait for process to terminate (with timeout)
                try:
                    self.streaming_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    # If it doesn't terminate, force kill
                    print("Streaming process didn't terminate, force killing...")
                    self.streaming_process.kill()
            except Exception as e:
                print(f"Error stopping streaming process: {e}")
            
            self.streaming_process = None
            self.streaming_mic = None
    
    def _start_playback(self) -> bool:
        """Start playing audio from remote stream with channel muting."""
        # First, stop any existing playback
        self._stop_playback()
        
        try:
            # Build VLC command with appropriate channel muting
            vlc_args = [
                'cvlc',
                self.remote_stream_url,
                '--quiet',
                '--no-video'
            ]
            
            # Apply channel muting if needed
            if self.muted_channels["left"] and not self.muted_channels["right"]:
                # Mute left channel only
                vlc_args.extend(['--audio-filter', 'channelmixer', '--channelmixer-left=0', '--channelmixer-right=1'])
            elif self.muted_channels["right"] and not self.muted_channels["left"]:
                # Mute right channel only
                vlc_args.extend(['--audio-filter', 'channelmixer', '--channelmixer-left=1', '--channelmixer-right=0'])
            elif self.muted_channels["left"] and self.muted_channels["right"]:
                # Mute both channels (silent)
                vlc_args.extend(['--volume', '0'])
            else:
                # No muting, set normal volume
                vlc_args.extend(['--volume', str(AUDIO_VOLUME)])
            
            print(f"Starting audio playback: {' '.join(vlc_args)}")
            
            # Start VLC process
            self.playback_process = subprocess.Popen(
                vlc_args, 
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # Update state
            self.playing_audio = True
            
            return True
        except Exception as e:
            print(f"Error starting audio playback: {e}")
            self.playing_audio = False
            return False
    
    def _restart_playback_with_muting(self) -> None:
        """Restart playback with current muting settings."""
        if self.playing_audio:
            self._start_playback()
    
    def _stop_playback(self) -> None:
        """Stop audio playback."""
        if self.playback_process:
            try:
                # Send SIGTERM to VLC process
                self.playback_process.terminate()
                
                # Wait for process to terminate (with timeout)
                try:
                    self.playback_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    # If it doesn't terminate, force kill
                    print("Playback process didn't terminate, force killing...")
                    self.playback_process.kill()
            except Exception as e:
                print(f"Error stopping playback process: {e}")
            
            self.playback_process = None
            self.playing_audio = False

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
