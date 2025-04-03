"""
Audio streaming system for the Dans le Blanc des Yeux installation.
Uses JACK audio server for device management and network streaming.

Audio Logic:
1. When both have pressure:
   - Play with LEFT channel muted (personal speaker muted)
   - Send personal mic (TX) to remote
2. When remote has pressure and local doesn't:
   - Play with RIGHT channel muted (global speaker muted)
   - Send global mic (USB) to remote
3. When local has pressure and remote doesn't:
   - Play with LEFT channel muted (personal speaker muted)
   - Send personal mic (TX) to remote
4. When neither has pressure:
   - No audio playback
"""

import time
import threading
import subprocess
import jack
import os
import configparser
import socket
from typing import Dict, Any, List, Optional, Tuple
from system_state import system_state


class AudioSystem:
    """Manages audio routing and streaming between devices using JACK."""
    
    def __init__(self, remote_ip: str):
        self.remote_ip = remote_ip
        
        # JACK client
        self.client = None
        self.jack_server_started = False
        
        # Audio devices
        self.personal_mic_name = "TX 96Khz"
        self.global_mic_name = "USB Audio Device"
        self.personal_speaker_mute_channel = "left"  # Channel to mute for personal speaker
        self.global_speaker_mute_channel = "right"   # Channel to mute for global speaker
        
        # Audio state
        self.streaming_mic = "none"  # 'personal', 'global', or 'none'
        self.muted_channels = []     # List of muted channels ('left', 'right', or both)
        
        # NetJack sending
        self.netjack_process = None
        self.netjack_lock = threading.Lock()
        
        # Threading
        self.running = False
        self.threads = []
        
        # Get audio configuration from config.ini
        self._load_config()
        
        # Register as observer for state changes
        system_state.add_observer(self._on_state_change)
        
        # Initialize audio state in system state
        audio_state = {
            "playing": False,
            "streaming_mic": self.streaming_mic,
            "muted_channels": self.muted_channels
        }
        system_state.update_local_state({"audio": audio_state})
        
        print("Audio system initialized")
    
    def _load_config(self):
        """Load audio configuration from config.ini."""
        try:
            config = configparser.ConfigParser()
            config.read('config.ini')
            
            if 'audio' in config:
                # Load device names
                self.personal_mic_name = config.get('audio', 'personal_mic_name', fallback=self.personal_mic_name)
                self.global_mic_name = config.get('audio', 'global_mic_name', fallback=self.global_mic_name)
                
                # Load channel configuration
                self.personal_speaker_mute_channel = config.get('audio', 'personal_speaker_mute_channel', fallback=self.personal_speaker_mute_channel)
                self.global_speaker_mute_channel = config.get('audio', 'global_speaker_mute_channel', fallback=self.global_speaker_mute_channel)
                
                print(f"Loaded audio config: Personal mic='{self.personal_mic_name}', Global mic='{self.global_mic_name}'")
                print(f"Channel config: Personal speaker mute='{self.personal_speaker_mute_channel}', Global speaker mute='{self.global_speaker_mute_channel}'")
        except Exception as e:
            print(f"Error loading audio config: {e}")
            print("Using default audio settings")
    
    def start(self) -> bool:
        """Start the audio system and JACK server."""
        print("Starting audio system...")
        self.running = True
        
        # Start JACK server if not already running
        if not self._is_jack_running():
            if not self._start_jack_server():
                print("Failed to start JACK server")
                return False
            self.jack_server_started = True
        else:
            print("JACK server is already running")
        
        # Connect to JACK
        try:
            self.client = jack.Client("DansBlanc")
            self.client.activate()
            print("Connected to JACK server")
        except jack.JackError as e:
            print(f"Failed to connect to JACK server: {e}")
            return False
        
        # Start NetJack receiver
        self._start_netjack_receiver()
        
        # Start audio monitor thread
        monitor_thread = threading.Thread(target=self._audio_monitor_loop)
        monitor_thread.daemon = True
        monitor_thread.start()
        self.threads.append(monitor_thread)
        
        # Update initial audio state based on current pressure conditions
        self._update_audio_state_based_on_pressure()
        
        print("Audio system started")
        return True
    
    def stop(self) -> None:
        """Stop the audio system and clean up resources."""
        print("Stopping audio system...")
        self.running = False
        
        # Stop NetJack processes
        self._stop_netjack()
        
        # Wait for all threads to finish
        for thread in self.threads:
            thread.join(timeout=1.0)
        
        # Disconnect from JACK
        if self.client:
            try:
                self.client.deactivate()
                self.client.close()
            except Exception as e:
                print(f"Error closing JACK client: {e}")
        
        # Stop JACK server if we started it
        if self.jack_server_started:
            self._stop_jack_server()
        
        print("Audio system stopped")
    
    def _on_state_change(self, changed_state: str) -> None:
        """Handle system state changes."""
        if changed_state in ["local", "remote"]:
            # Update audio state when pressure changes
            self._update_audio_state_based_on_pressure()
    
    def _update_audio_state_based_on_pressure(self) -> None:
        """Update audio routing and streaming based on pressure states."""
        local_state = system_state.get_local_state()
        remote_state = system_state.get_remote_state()
        
        local_pressure = local_state.get("pressure", False)
        remote_pressure = remote_state.get("pressure", False)
        is_connected = remote_state.get("connected", False)
        
        # Default state: no streaming, all channels muted
        new_streaming_mic = "none"
        new_muted_channels = ["left", "right"]
        
        # Apply logic based on pressure states
        if is_connected:
            # Case 1: Both have pressure
            if local_pressure and remote_pressure:
                new_streaming_mic = "personal"
                new_muted_channels = [self.personal_speaker_mute_channel]  # Mute LEFT channel (personal)
                
            # Case 2: Remote has pressure, local doesn't
            elif remote_pressure and not local_pressure:
                new_streaming_mic = "global"
                new_muted_channels = [self.global_speaker_mute_channel]    # Mute RIGHT channel (global)
                
            # Case 3: Local has pressure, remote doesn't
            elif local_pressure and not remote_pressure:
                new_streaming_mic = "personal"
                new_muted_channels = [self.personal_speaker_mute_channel]  # Mute LEFT channel (personal)
                
            # Case 4: Neither has pressure
            else:
                new_streaming_mic = "none"
                new_muted_channels = ["left", "right"]  # Mute both channels (no playback)
        
        # Only update if there are changes
        if new_streaming_mic != self.streaming_mic or set(new_muted_channels) != set(self.muted_channels):
            print(f"Audio state change: streaming_mic={new_streaming_mic}, muted_channels={new_muted_channels}")
            
            # Update muting
            self._update_channel_muting(new_muted_channels)
            
            # Update streaming
            if new_streaming_mic != self.streaming_mic:
                self._update_streaming(new_streaming_mic)
            
            # Update local state
            self.muted_channels = new_muted_channels
            self.streaming_mic = new_streaming_mic
            
            # Update system state
            audio_state = {
                "playing": len(new_muted_channels) < 2,  # Playing if at least one channel is unmuted
                "streaming_mic": new_streaming_mic,
                "muted_channels": new_muted_channels
            }
            system_state.update_local_state({"audio": audio_state})
    
    def _update_channel_muting(self, channels_to_mute: List[str]) -> None:
        """Update channel muting based on the list of channels to mute."""
        try:
            # Use JACK to mute/unmute channels
            if self.client:
                # Implementation will depend on how audio is routed in JACK
                # This is a simplified example
                output_ports = self.client.get_ports(is_audio=True, is_output=True, is_physical=True)
                
                for port in output_ports:
                    port_name = port.name
                    
                    # Check if this is a left or right channel
                    is_left = "left" in port_name.lower() or port_name.endswith("1")
                    is_right = "right" in port_name.lower() or port_name.endswith("2")
                    
                    # Determine if this channel should be muted
                    should_mute = (is_left and "left" in channels_to_mute) or (is_right and "right" in channels_to_mute)
                    
                    # Apply muting (implementation will depend on your JACK setup)
                    # In practice, this might involve disconnecting ports or using JACK control tools
                    
            # Alternatively, use ALSA amixer for simple channel muting
            for channel in ["left", "right"]:
                mute_cmd = ["amixer", "set", "Master", channel, "mute" if channel in channels_to_mute else "unmute"]
                subprocess.run(mute_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
        except Exception as e:
            print(f"Error updating channel muting: {e}")
    
    def _update_streaming(self, mic_to_stream: str) -> None:
        """Update audio streaming to remote device."""
        try:
            # Stop any existing streaming
            self._stop_netjack()
            
            # Start new streaming if needed
            if mic_to_stream != "none" and self.remote_ip:
                mic_name = self.personal_mic_name if mic_to_stream == "personal" else self.global_mic_name
                self._start_netjack_sender(mic_name)
        except Exception as e:
            print(f"Error updating audio streaming: {e}")
    
    def _start_netjack_sender(self, source_device: str) -> bool:
        """Start NetJack sender to stream audio to remote device."""
        with self.netjack_lock:
            try:
                # Format command to start jack.udp_sender
                # Parameters will need adjustment based on your network and quality requirements
                cmd = [
                    "jack.udp_sender",
                    "--host", self.remote_ip,
                    "--source", source_device,
                    "--channels", "2"  # Stereo audio
                ]
                
                print(f"Starting NetJack sender: {' '.join(cmd)}")
                self.netjack_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                
                # Give it a moment to start
                time.sleep(0.5)
                
                # Check if process is still running
                if self.netjack_process.poll() is None:
                    print(f"NetJack sender started successfully, streaming {source_device}")
                    return True
                else:
                    print("NetJack sender failed to start")
                    return False
                    
            except Exception as e:
                print(f"Error starting NetJack sender: {e}")
                return False
    
    def _stop_netjack(self) -> None:
        """Stop any running NetJack processes."""
        with self.netjack_lock:
            if self.netjack_process:
                try:
                    self.netjack_process.terminate()
                    self.netjack_process.wait(timeout=2.0)
                except Exception as e:
                    print(f"Error stopping NetJack process: {e}")
                    try:
                        self.netjack_process.kill()
                    except:
                        pass
                finally:
                    self.netjack_process = None
            
            # Additional cleanup to ensure no hanging processes
            try:
                subprocess.run(
                    ["killall", "-9", "jack.udp_sender"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except:
                pass
    
    def _start_netjack_receiver(self) -> bool:
        """Start NetJack receiver to receive audio from remote device."""
        try:
            # Start jack.udp_receiver in background
            cmd = [
                "jack.udp_receiver",
                "--channels", "2"  # Stereo audio
            ]
            
            print(f"Starting NetJack receiver: {' '.join(cmd)}")
            receiver_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # Give it a moment to start
            time.sleep(0.5)
            
            # Check if process is still running
            if receiver_process.poll() is None:
                print("NetJack receiver started successfully")
                return True
            else:
                print("NetJack receiver failed to start")
                return False
                
        except Exception as e:
            print(f"Error starting NetJack receiver: {e}")
            return False
    
    def _is_jack_running(self) -> bool:
        """Check if JACK server is already running."""
        try:
            result = subprocess.run(
                ["jack_control", "status"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            return "running" in result.stdout.lower()
        except Exception:
            return False
    
    def _start_jack_server(self) -> bool:
        """Start JACK server."""
        try:
            # Start JACK server with appropriate settings
            cmd = ["jackd", "-d", "alsa", "-r", "48000", "-p", "1024", "-n", "2"]
            
            print(f"Starting JACK server: {' '.join(cmd)}")
            jack_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # Give it time to start
            time.sleep(2.0)
            
            # Check if it's running
            if self._is_jack_running():
                print("JACK server started successfully")
                return True
            else:
                print("JACK server failed to start")
                return False
                
        except Exception as e:
            print(f"Error starting JACK server: {e}")
            return False
    
    def _stop_jack_server(self) -> None:
        """Stop JACK server."""
        try:
            subprocess.run(
                ["jack_control", "stop"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            print("JACK server stopped")
        except Exception as e:
            print(f"Error stopping JACK server: {e}")
    
    def _find_device_by_name(self, device_name: str) -> Optional[str]:
        """Find JACK device ID by name."""
        try:
            # Get list of JACK input ports
            if self.client:
                ports = self.client.get_ports(is_audio=True, is_input=True, is_physical=True)
                
                # Find the device with matching name
                for port in ports:
                    if device_name.lower() in port.name.lower():
                        return port.name
            
            # If client is not available or device not found, try using system tools
            result = subprocess.run(
                ["jack_lsp"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if device_name.lower() in line.lower():
                        return line.strip()
            
            print(f"Warning: Could not find audio device named '{device_name}'")
            return None
            
        except Exception as e:
            print(f"Error finding audio device: {e}")
            return None
    
    def _connect_ports(self, source_port: str, dest_port: str) -> bool:
        """Connect two JACK ports."""
        try:
            if self.client:
                # Connect using JACK client
                self.client.connect(source_port, dest_port)
                return True
            else:
                # Connect using jack_connect command
                result = subprocess.run(
                    ["jack_connect", source_port, dest_port],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True
                )
                
                # Check if successful or already connected
                return result.returncode == 0 or "already connected" in result.stderr.lower()
                
        except Exception as e:
            print(f"Error connecting JACK ports: {e}")
            return False
    
    def _disconnect_ports(self, source_port: str, dest_port: str) -> bool:
        """Disconnect two JACK ports."""
        try:
            if self.client:
                # Disconnect using JACK client
                self.client.disconnect(source_port, dest_port)
                return True
            else:
                # Disconnect using jack_disconnect command
                result = subprocess.run(
                    ["jack_disconnect", source_port, dest_port],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True
                )
                
                # Check if successful or already disconnected
                return result.returncode == 0 or "not connected" in result.stderr.lower()
                
        except Exception as e:
            print(f"Error disconnecting JACK ports: {e}")
            return False
    
    def _audio_monitor_loop(self) -> None:
        """Monitor audio system and ensure proper operation."""
        print("Audio monitor thread started")
        
        check_interval = 5.0  # seconds
        last_check_time = 0
        
        while self.running:
            current_time = time.time()
            
            # Perform periodic checks
            if current_time - last_check_time >= check_interval:
                # Ensure audio routing is correct
                self._check_audio_routing()
                
                # Check if we need to update audio state based on system state
                self._update_audio_state_based_on_pressure()
                
                last_check_time = current_time
            
            # Sleep a bit to avoid consuming CPU
            time.sleep(0.5)
        
        print("Audio monitor thread stopped")
    
    def _check_audio_routing(self) -> None:
        """Check and correct audio routing if necessary."""
        try:
            # Implementation will depend on your specific JACK setup
            # This is a placeholder for a function that would check and fix routing issues
            pass
            
        except Exception as e:
            print(f"Error checking audio routing: {e}")


# Run as standalone test if executed directly
if __name__ == "__main__":
    print("Testing audio system...")
    
    # Initialize with loopback address for testing
    audio_system = AudioSystem("127.0.0.1")
    
    if audio_system.start():
        try:
            print("\nSimulating different pressure states:")
            
            # Test case 1: Both have pressure
            print("\n1. Both have pressure - Should mute LEFT channel (personal), stream personal mic")
            system_state.update_local_state({"pressure": True})
            system_state.update_remote_state({"pressure": True, "connected": True})
            time.sleep(5)
            
            # Test case 2: Remote has pressure, local doesn't
            print("\n2. Remote has pressure, local doesn't - Should mute RIGHT channel (global), stream global mic")
            system_state.update_local_state({"pressure": False})
            system_state.update_remote_state({"pressure": True, "connected": True})
            time.sleep(5)
            
            # Test case 3: Local has pressure, remote doesn't
            print("\n3. Local has pressure, remote doesn't - Should mute LEFT channel (personal), stream personal mic")
            system_state.update_local_state({"pressure": True})
            system_state.update_remote_state({"pressure": False, "connected": True})
            time.sleep(5)
            
            # Test case 4: Neither has pressure
            print("\n4. Neither has pressure - Should mute both channels (no playback), no streaming")
            system_state.update_local_state({"pressure": False})
            system_state.update_remote_state({"pressure": False, "connected": True})
            time.sleep(5)
            
            print("\nAudio test complete.")
            print("Press Ctrl+C to stop...")
            
            while True:
                time.sleep(1)
                
        except KeyboardInterrupt:
            print("\nTest interrupted by user")
        finally:
            audio_system.stop()
            print("Audio system stopped")
    else:
        print("Failed to start audio system")
