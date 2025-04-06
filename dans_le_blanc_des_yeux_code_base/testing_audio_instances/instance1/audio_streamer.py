"""
Audio streaming module for the Dans le Blanc des Yeux installation using GStreamer.
Handles capturing and sending audio streams between devices using GStreamer pipelines.

Fixed streaming logic:
1. When both have pressure:
   - Stream personal mic (TX) to remote device
   
2. When remote has pressure and local doesn't:
   - Stream global mic (USB) to remote device
   
3. When local has pressure and remote doesn't:
   - Stream personal mic (TX) to remote device
   
4. When neither has pressure: No streaming
"""

import os
import time
import threading
import socket
import configparser
from typing import Dict, Optional, Tuple, List, Callable, Any

# Import GStreamer
import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstAudio', '1.0')
from gi.repository import Gst, GstAudio, GLib

from system_state import system_state

# Initialize GStreamer
Gst.init(None)

# Audio configuration
RATE = 44100
CHANNELS = 2
DEVICE_SEARCH_INTERVAL = 30  # Seconds between device searches
MAX_RETRY_ATTEMPTS = 5
RETRY_DELAY = 2  # Seconds between retries

# Network configuration
AUDIO_PORT = 6000  # Base port for audio streaming
REMOTE_PORT = 6001  # Base port for audio streaming

class AudioStreamer:
    """Handles audio streaming between devices using GStreamer."""
    
    def __init__(self, remote_ip: str):
        self.remote_ip = remote_ip
        
        # Audio device names (will be loaded from config)
        self.personal_mic_name = "TX 96Khz"
        self.global_mic_name = "USB Audio Device"
        
        # Keep track of actual ALSA device names/ids
        self.personal_mic_alsa_device = None
        self.global_mic_alsa_device = None
        
        # GStreamer pipelines
        self.personal_pipeline = None
        self.global_pipeline = None
        self.receiver_pipeline = None
        
        # GLib main loop for GStreamer
        self.loop = GLib.MainLoop()
        self.loop_thread = None
        
        # Streaming state
        self.current_mic_sending = None  # "personal" or "global" or None
        
        # Threading
        self.running = False
        self.threads = []
        self.lock = threading.Lock()
        
        # Device monitoring
        self.device_monitor_thread = None
        
        # Retry counters
        self.personal_mic_retries = 0
        self.global_mic_retries = 0
        self.receive_retries = 0
        
        # Callbacks for received audio
        self.on_personal_mic_received = None
        self.on_global_mic_received = None
        
        # Last captured audio data for each mic type
        self.last_personal_audio = b''
        self.last_global_audio = b''
        
        # Load settings from config
        self._load_config()
        
        # Register as observer for state changes
        system_state.add_observer(self._on_state_change)
        
        print(f"Audio streamer initialized with remote IP: {remote_ip}")
    
    def _load_config(self):
        """Load audio settings from config.ini"""
        try:
            config = configparser.ConfigParser()
            config.read('config.ini')
            
            if 'audio' in config:
                # Load device names
                self.personal_mic_name = config.get('audio', 'personal_mic_name', fallback="TX 96Khz")
                self.global_mic_name = config.get('audio', 'global_mic_name', fallback="USB Audio Device")
                
                print(f"Loaded audio device names from config.ini:")
                print(f"  personal mic name: {self.personal_mic_name}")
                print(f"  global mic name: {self.global_mic_name}")
            else:
                print("No [audio] section found in config.ini, using default settings")
                self._add_default_config_settings(config)
                
        except Exception as e:
            print(f"Error loading audio config: {e}")
            print("Using default audio device names")
    
    def _add_default_config_settings(self, config):
        """Add default audio settings to config.ini if not present"""
        try:
            if 'audio' not in config:
                config['audio'] = {}
            
            # Add device names
            if 'personal_mic_name' not in config['audio']:
                config['audio']['personal_mic_name'] = self.personal_mic_name
            if 'global_mic_name' not in config['audio']:
                config['audio']['global_mic_name'] = self.global_mic_name
            
            # Write to config file
            with open('config.ini', 'w') as configfile:
                config.write(configfile)
                
            print("Added default audio settings to config.ini")
        except Exception as e:
            print(f"Error adding default audio settings to config: {e}")
    
    def _find_audio_devices(self) -> None:
        """Find the ALSA audio devices by name using GStreamer."""
        # Reset device IDs
        old_personal_device = self.personal_mic_alsa_device
        old_global_device = self.global_mic_alsa_device
        
        self.personal_mic_alsa_device = None
        self.global_mic_alsa_device = None
        
        try:
            # Create a device monitor to enumerate audio devices
            device_monitor = Gst.DeviceMonitor.new()
            device_monitor.add_filter("Audio/Source", None)  # Filter for audio sources
            device_monitor.start()
            
            # Get all audio devices
            devices = device_monitor.get_devices()
            device_monitor.stop()
            
            print("\nAudio devices available:")
            for i, device in enumerate(devices):
                properties = device.get_properties()
                device_class = properties.get_string("device.class") if properties.has_field("device.class") else "unknown"
                
                # Get the display name and ALSA device path
                display_name = device.get_display_name()
                alsa_path = None
                
                # Try different property names for the ALSA device path
                for prop_name in ["device.path", "alsa.device", "alsa.name"]:
                    if properties.has_field(prop_name):
                        alsa_path = properties.get_string(prop_name)
                        if alsa_path:
                            break
                
                print(f"Device {i}: {display_name}")
                print(f"  Class: {device_class}")
                print(f"  ALSA path: {alsa_path}")
                
                # Find personal mic (TX)
                if self.personal_mic_name in display_name and alsa_path:
                    self.personal_mic_alsa_device = alsa_path
                    print(f"Found {self.personal_mic_name} mic: {alsa_path}")
                
                # Find global mic (USB)
                elif self.global_mic_name in display_name and alsa_path:
                    self.global_mic_alsa_device = alsa_path
                    print(f"Found {self.global_mic_name} mic: {alsa_path}")
            
            # Fallback for global mic: any USB Audio with device class "Audio/Source"
            if self.global_mic_alsa_device is None:
                for device in devices:
                    display_name = device.get_display_name()
                    if "USB Audio" in display_name and self.personal_mic_name not in display_name:
                        properties = device.get_properties()
                        for prop_name in ["device.path", "alsa.device", "alsa.name"]:
                            if properties.has_field(prop_name):
                                alsa_path = properties.get_string(prop_name)
                                if alsa_path:
                                    self.global_mic_alsa_device = alsa_path
                                    print(f"Found fallback USB mic: {alsa_path}")
                                    break
                        if self.global_mic_alsa_device:
                            break
            
            # Log the devices we found
            print(f"{self.personal_mic_name} mic: {self.personal_mic_alsa_device}")
            print(f"{self.global_mic_name} mic: {self.global_mic_alsa_device}")
            
            # If we're missing devices, warn
            missing_devices = []
            if self.personal_mic_alsa_device is None:
                missing_devices.append(f"{self.personal_mic_name} microphone")
            if self.global_mic_alsa_device is None:
                missing_devices.append(f"{self.global_mic_name} microphone")
            
            if missing_devices:
                print(f"Warning: Could not find these audio devices: {', '.join(missing_devices)}")
                print("Audio streaming may not work correctly")
            else:
                print("All required audio devices found")
            
            # Reset retry counters if devices are found
            if self.personal_mic_alsa_device is not None:
                self.personal_mic_retries = 0
            if self.global_mic_alsa_device is not None:
                self.global_mic_retries = 0
            
            # Check if devices have changed, which may require pipeline restart
            if (old_personal_device != self.personal_mic_alsa_device or 
                old_global_device != self.global_mic_alsa_device):
                print("Audio devices have changed, updating pipelines...")
                self._update_streaming_based_on_state()
                
        except Exception as e:
            print(f"Error finding audio devices: {e}")
    
    def start(self) -> bool:
        """Start the audio streaming system."""
        print("Starting audio streamer...")
        self.running = True
        
        # Find audio devices first
        self._find_audio_devices()
        
        # Start GLib main loop in a separate thread
        self.loop_thread = threading.Thread(target=self._run_glib_loop)
        self.loop_thread.daemon = True
        self.loop_thread.start()
        self.threads.append(self.loop_thread)
        
        # Start device monitor thread
        # self.device_monitor_thread = threading.Thread(target=self._device_monitor_loop)
        # self.device_monitor_thread.daemon = True
        # self.device_monitor_thread.start()
        # self.threads.append(self.device_monitor_thread)
        
        # Start receiver pipeline
        self._start_receiver()
        
        # Check initial state to see if we need to start streaming right away
        self._update_streaming_based_on_state()
        
        print("Audio streamer started")
        return True
    
    def _run_glib_loop(self):
        """Run the GLib main loop for GStreamer."""
        try:
            print("Starting GLib main loop for GStreamer")
            # Set context to avoid threading issues
            context = GLib.MainContext.default()
            context.push_thread_default()
            self.loop.run()
            context.pop_thread_default()
        except Exception as e:
            print(f"Error in GLib main loop: {e}")
    
    def stop(self) -> None:
        """Stop all streaming and release resources."""
        print("Stopping audio streamer...")
        self.running = False
        
        # Stop all pipelines with proper cleanup
        self._stop_streaming()
        self._stop_receiver()
        
        # Stop the GLib main loop
        if self.loop and self.loop.is_running():
            self.loop.quit()
        
        # Wait for threads to finish
        for thread in self.threads:
            if thread.is_alive():
                thread.join(timeout=1.0)
        
        print("Audio streamer stopped")
    
    def register_personal_mic_callback(self, callback: Callable[[bytes], None]) -> None:
        """Register a callback for when personal mic audio is received."""
        self.on_personal_mic_received = callback
    
    def register_global_mic_callback(self, callback: Callable[[bytes], None]) -> None:
        """Register a callback for when USB Audio Device mic audio is received."""
        self.on_global_mic_received = callback
    
    def _on_state_change(self, changed_state: str) -> None:
        """Handle system state changes."""
        if changed_state in ["local", "remote"]:
            self._update_streaming_based_on_state()
    
    def _update_streaming_based_on_state(self) -> None:
        """Update streaming state based on the current system state."""
        local_state = system_state.get_local_state()
        remote_state = system_state.get_remote_state()
        
        # Only proceed if remote is connected
        if not remote_state.get("connected", False):
            self._stop_streaming()
            return
        
        # Case 1: Both have pressure - stream personal mic (TX)
        if local_state.get("pressure", False) and remote_state.get("pressure", False):
            self._start_streaming("personal")
        
        # Case 2: Remote has pressure but local doesn't - stream global mic (USB)
        elif remote_state.get("pressure", False) and not local_state.get("pressure", False):
            self._start_streaming("global")
        
        # Case 3: Local has pressure but remote doesn't - stream personal mic (TX)
        elif local_state.get("pressure", False) and not remote_state.get("pressure", False):
            self._start_streaming("personal")
        
        # Case 4: No pressure on either - no streaming
        else:
            self._stop_streaming()
    
    # def _device_monitor_loop(self) -> None:
    #     """Periodically check for audio devices in case they disconnect/reconnect."""
    #     last_check_time = 0
    #     
    #     while self.running:
    #         current_time = time.time()
    #         
    #         # Check devices periodically
    #         if current_time - last_check_time > DEVICE_SEARCH_INTERVAL:
    #             print("Checking audio devices...")
    #             self._find_audio_devices()
    #             
    #             # If the current mic is missing, try to restart streaming
    #             if (self.current_mic_sending == "personal" and not self.personal_mic_alsa_device) or \
    #                (self.current_mic_sending == "global" and not self.global_mic_alsa_device):
    #                 print(f"Current microphone ({self.current_mic_sending}) is missing, restarting streaming")
    #                 self._update_streaming_based_on_state()
    #             
    #             last_check_time = current_time
    #         
    #         # Sleep to avoid consuming CPU
    #         time.sleep(5)
    
    def _create_sender_pipeline(self, mic_type: str) -> Optional[Gst.Pipeline]:
        """Create a GStreamer pipeline for sending audio from the specified mic."""
        try:
            # Use device names directly with pulsesrc
            if mic_type == "personal":
                device_name = "virtual_tx.monitor"
            else:  # global
                device_name = "virtual_usb.monitor"
            
            # Create a unique pipeline name
            pipeline_name = f"{mic_type}_pipeline_{int(time.time())}"
            
            # Create a sender pipeline using pulsesrc and appsink
            pipeline_str = (
                f"pulsesrc device={device_name} ! "
                f"audio/x-raw, rate={RATE}, channels={CHANNELS} ! "
                "audioconvert ! "
                "audioresample ! "
                "audio/x-raw, rate=44100, channels=2, format=S16LE ! "
                "appsink name=audio_sink emit-signals=true max-buffers=10 drop=true sync=false"
            )
            
            print(f"Creating {mic_type} pipeline: {pipeline_str}")
            
            pipeline = Gst.parse_launch(pipeline_str)
            pipeline.set_name(pipeline_name)
            
            # Set up the appsink to get audio samples
            sink = pipeline.get_by_name("audio_sink")
            sink.set_property("emit-signals", True)
            
            # Connect to the new-sample signal
            if mic_type == "personal":
                sink.connect("new-sample", self._on_personal_sample)
            else:
                sink.connect("new-sample", self._on_global_sample)
            
            # Add message handlers
            bus = pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message::error", self._on_pipeline_error, mic_type)
            bus.connect("message::warning", self._on_pipeline_warning, mic_type)
            bus.connect("message::eos", self._on_pipeline_eos, mic_type)
            
            return pipeline
            
        except Exception as e:
            print(f"Error creating {mic_type} sender pipeline: {e}")
            return None
    
    def _on_personal_sample(self, sink) -> Gst.FlowReturn:
        """Handle new audio samples from the personal mic pipeline."""
        sample = sink.emit("pull-sample")
        if sample:
            buffer = sample.get_buffer()
            success, map_info = buffer.map(Gst.MapFlags.READ)
            
            if success:
                # Get the bytes from the buffer
                audio_data = bytes(map_info.data)
                buffer.unmap(map_info)
                
                # Store the audio data
                self.last_personal_audio = audio_data
                
                # Send to remote if this is the current mic
                if self.current_mic_sending == "personal":
                    self._send_audio_packet("personal", audio_data)
            
            return Gst.FlowReturn.OK
        
        return Gst.FlowReturn.ERROR
    
    def _on_global_sample(self, sink) -> Gst.FlowReturn:
        """Handle new audio samples from the global mic pipeline."""
        sample = sink.emit("pull-sample")
        if sample:
            buffer = sample.get_buffer()
            success, map_info = buffer.map(Gst.MapFlags.READ)
            
            if success:
                # Get the bytes from the buffer
                audio_data = bytes(map_info.data)
                buffer.unmap(map_info)
                
                # Store the audio data
                self.last_global_audio = audio_data
                
                # Send to remote if this is the current mic
                if self.current_mic_sending == "global":
                    self._send_audio_packet("global", audio_data)
            
            return Gst.FlowReturn.OK
        
        return Gst.FlowReturn.ERROR
    
    def _send_audio_packet(self, mic_type: str, audio_data: bytes) -> None:
        """Send audio data to the remote device via UDP."""
        try:
            # Create a UDP socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
            
            # Add mic type identifier (0 for personal, 1 for global)
            mic_id_byte = b'\x00' if mic_type == "personal" else b'\x01'
            
            # Create packet with sequence number (placeholder) and mic type
            packet = b'\x00\x00\x00\x00' + mic_id_byte + audio_data
            
            # Send packet
            sock.sendto(packet, (self.remote_ip, REMOTE_PORT))
            
            # Close socket
            sock.close()
            
        except Exception as e:
            print(f"Error sending audio packet: {e}")
    
    def _create_receiver_pipeline(self) -> Optional[Gst.Pipeline]:
        """Create a GStreamer pipeline for receiving audio.
        
        Returns:
            The created GStreamer pipeline or None if failed
        """
        try:
            # Create a unique pipeline name
            pipeline_name = f"receiver_pipeline_{int(time.time())}"
            
            # Create a receiver pipeline using udpsrc and appsink
            pipeline_str = (
                f"udpsrc port={AUDIO_PORT} ! "
                "application/x-udp, encoding-name=RAW ! "
                "queue max-size-bytes=65536 ! "
                "appsink name=sink emit-signals=true max-buffers=10 drop=true sync=false"
            )
            
            print(f"Creating receiver pipeline: {pipeline_str}")
            
            pipeline = Gst.parse_launch(pipeline_str)
            pipeline.set_name(pipeline_name)
            
            # Set up the appsink to handle received audio data
            sink = pipeline.get_by_name("sink")
            sink.set_property("emit-signals", True)
            sink.connect("new-sample", self._on_new_sample)
            
            # Add message handlers for errors, warnings, and EOS
            bus = pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message::error", self._on_pipeline_error, "receiver")
            bus.connect("message::warning", self._on_pipeline_warning, "receiver")
            bus.connect("message::eos", self._on_pipeline_eos, "receiver")
            
            return pipeline
            
        except Exception as e:
            print(f"Error creating receiver pipeline: {e}")
            return None
    
    def _on_new_sample(self, sink) -> Gst.FlowReturn:
        """Handle new audio samples from the receiver pipeline."""
        sample = sink.emit("pull-sample")
        if sample:
            buffer = sample.get_buffer()
            success, map_info = buffer.map(Gst.MapFlags.READ)
            
            if success:
                try:
                    # Get the bytes from the buffer
                    packet_data = bytes(map_info.data)
                    buffer.unmap(map_info)
                    
                    # Extract mic type and audio data
                    if len(packet_data) > 5:  # Need at least 5 bytes (4 for seq + 1 for mic type)
                        # Next byte indicates mic type (0=personal, 1=global)
                        mic_type_byte = packet_data[4:5]
                        
                        # Rest of the packet contains the audio data
                        audio_data = packet_data[5:]
                        
                        # Call appropriate callback based on mic type
                        if mic_type_byte == b'\x00' and self.on_personal_mic_received:
                            self.on_personal_mic_received(audio_data)
                        elif mic_type_byte == b'\x01' and self.on_global_mic_received:
                            self.on_global_mic_received(audio_data)
                
                except Exception as e:
                    print(f"Error processing received audio: {e}")
            
            return Gst.FlowReturn.OK
        
        return Gst.FlowReturn.ERROR
    
    def _on_pipeline_error(self, bus, message, pipeline_type):
        """Handle pipeline errors."""
        err, debug = message.parse_error()
        print(f"Error in {pipeline_type} pipeline: {err.message}")
        print(f"Debug info: {debug}")
        
        # Handle errors with retries
        if pipeline_type == "personal":
            if self.personal_mic_retries < MAX_RETRY_ATTEMPTS:
                print(f"Retrying personal mic pipeline in {RETRY_DELAY} seconds...")
                self.personal_mic_retries += 1
                threading.Timer(RETRY_DELAY, self._restart_personal_pipeline).start()
            else:
                print("Max retries reached for personal mic pipeline")
                self._stop_personal_pipeline()
                
        elif pipeline_type == "global":
            if self.global_mic_retries < MAX_RETRY_ATTEMPTS:
                print(f"Retrying global mic pipeline in {RETRY_DELAY} seconds...")
                self.global_mic_retries += 1
                threading.Timer(RETRY_DELAY, self._restart_global_pipeline).start()
            else:
                print("Max retries reached for global mic pipeline")
                self._stop_global_pipeline()
                
        elif pipeline_type == "receiver":
            if self.receive_retries < MAX_RETRY_ATTEMPTS:
                print(f"Retrying receiver pipeline in {RETRY_DELAY} seconds...")
                self.receive_retries += 1
                threading.Timer(RETRY_DELAY, self._restart_receiver).start()
            else:
                print("Max retries reached for receiver pipeline")
                self._stop_receiver()
    
    def _on_pipeline_warning(self, bus, message, pipeline_type):
        """Handle pipeline warnings."""
        warn, debug = message.parse_warning()
        print(f"Warning in {pipeline_type} pipeline: {warn.message}")
        print(f"Debug info: {debug}")
    
    def _on_pipeline_eos(self, bus, message, pipeline_type):
        """Handle pipeline end-of-stream."""
        print(f"End of stream in {pipeline_type} pipeline")
        
        # Restart pipelines on EOS
        if pipeline_type == "personal" and self.current_mic_sending == "personal":
            self._restart_personal_pipeline()
        elif pipeline_type == "global" and self.current_mic_sending == "global":
            self._restart_global_pipeline()
        elif pipeline_type == "receiver":
            self._restart_receiver()
    
    def _start_streaming(self, mic_type: str) -> bool:
        """Start streaming from specified mic to remote device.
        
        Args:
            mic_type: Either "personal" for TX mic or "global" for USB mic
        """
        # If already streaming the correct mic, do nothing
        if self.current_mic_sending == mic_type:
            return True
            
        # Stop any current streaming
        self._stop_streaming()
        
        try:
            if mic_type == "personal":
                pipeline = self._create_sender_pipeline("personal")
                if not pipeline:
                    print(f"Failed to create personal mic pipeline")
                    return False
                
                self.personal_pipeline = pipeline
                result = self.personal_pipeline.set_state(Gst.State.PLAYING)
                
                if result == Gst.StateChangeReturn.FAILURE:
                    print(f"Failed to start personal mic pipeline")
                    self._stop_personal_pipeline()
                    return False
                
                self.current_mic_sending = "personal"
                print(f"Started {self.personal_mic_name} stream to {self.remote_ip}:{AUDIO_PORT}")
                
            else:  # global
                pipeline = self._create_sender_pipeline("global")
                if not pipeline:
                    print(f"Failed to create global mic pipeline")
                    return False
                
                self.global_pipeline = pipeline
                result = self.global_pipeline.set_state(Gst.State.PLAYING)
                
                if result == Gst.StateChangeReturn.FAILURE:
                    print(f"Failed to start global mic pipeline")
                    self._stop_global_pipeline()
                    return False
                
                self.current_mic_sending = "global"
                print(f"Started {self.global_mic_name} stream to {self.remote_ip}:{AUDIO_PORT}")
            
            return True
            
        except Exception as e:
            print(f"Error starting {mic_type} stream: {e}")
            self._stop_streaming()
            return False
    
    def _stop_streaming(self) -> None:
        """Stop all active streaming."""
        self._stop_personal_pipeline()
        self._stop_global_pipeline()
        self.current_mic_sending = None
        print("All audio streams stopped")
    
    def _stop_personal_pipeline(self) -> None:
        """Stop and clean up the personal mic pipeline."""
        if self.personal_pipeline:
            try:
                # Set state to NULL for clean shutdown
                self.personal_pipeline.set_state(Gst.State.NULL)
                time.sleep(0.1)  # Brief delay to allow state change to complete
                self.personal_pipeline = None
                print("Personal mic pipeline stopped")
            except Exception as e:
                print(f"Error stopping personal mic pipeline: {e}")
    
    def _stop_global_pipeline(self) -> None:
        """Stop and clean up the global mic pipeline."""
        if self.global_pipeline:
            try:
                # Set state to NULL for clean shutdown
                self.global_pipeline.set_state(Gst.State.NULL)
                time.sleep(0.1)  # Brief delay to allow state change to complete
                self.global_pipeline = None
                print("Global mic pipeline stopped")
            except Exception as e:
                print(f"Error stopping global mic pipeline: {e}")
    
    def _restart_personal_pipeline(self) -> None:
        """Restart the personal mic pipeline."""
        if self.current_mic_sending == "personal":
            self._stop_personal_pipeline()
            time.sleep(RETRY_DELAY)  # Wait before restarting
            self._start_streaming("personal")
    
    def _restart_global_pipeline(self) -> None:
        """Restart the global mic pipeline."""
        if self.current_mic_sending == "global":
            self._stop_global_pipeline()
            time.sleep(RETRY_DELAY)  # Wait before restarting
            self._start_streaming("global")
    
    def _start_receiver(self) -> bool:
        """Start the audio receiver pipeline."""
        try:
            # Create receiver pipeline
            pipeline = self._create_receiver_pipeline()
            if not pipeline:
                print("Failed to create receiver pipeline")
                return False
            
            self.receiver_pipeline = pipeline
            result = self.receiver_pipeline.set_state(Gst.State.PLAYING)
            
            if result == Gst.StateChangeReturn.FAILURE:
                print("Failed to start receiver pipeline")
                self._stop_receiver()
                return False
            
            print(f"Started audio receiver on port {AUDIO_PORT}")
            self.receive_retries = 0  # Reset retry counter on success
            return True
            
        except Exception as e:
            print(f"Error starting receiver: {e}")
            self._stop_receiver()
            return False
    
    def _stop_receiver(self) -> None:
        """Stop the audio receiver pipeline."""
        if self.receiver_pipeline:
            try:
                # Set state to NULL for clean shutdown
                self.receiver_pipeline.set_state(Gst.State.NULL)
                time.sleep(0.1)  # Brief delay to allow state change to complete
                self.receiver_pipeline = None
                print("Receiver pipeline stopped")
            except Exception as e:
                print(f"Error stopping receiver pipeline: {e}")
    
    def _restart_receiver(self) -> None:
        """Restart the audio receiver pipeline."""
        self._stop_receiver()
        time.sleep(RETRY_DELAY)  # Wait before restarting
        self._start_receiver()


# Test function to run the audio streamer standalone
def test_audio_streamer():
    """Test the audio streamer with loopback."""
    
    # Set up system state for testing
    system_state.update_local_state({"pressure": False})
    system_state.update_remote_state({"pressure": True, "connected": True})
    
    # Initialize audio streamer with loopback address for testing
    audio_streamer = AudioStreamer("127.0.0.1")
    
    # Register callbacks for received audio
    def on_personal_mic_audio(data):
        print(f"Received personal mic audio: {len(data)} bytes")
    
    def on_global_mic_audio(data):
        print(f"Received global mic audio: {len(data)} bytes")
    
    audio_streamer.register_personal_mic_callback(on_personal_mic_audio)
    audio_streamer.register_global_mic_callback(on_global_mic_audio)
    
    audio_streamer.start()
    
    try:
        print("\nTesting different pressure states:")
        print("\n1. Remote pressure, local no pressure - Streaming GLOBAL mic")
        time.sleep(5)
        
        print("\n2. Both have pressure - Streaming PERSONAL mic")
        system_state.update_local_state({"pressure": True})
        time.sleep(5)
        
        print("\n3. Local pressure, remote no pressure - Streaming PERSONAL mic")
        system_state.update_remote_state({"pressure": False})
        time.sleep(5)
        
        print("\n4. Neither has pressure - No streaming")
        system_state.update_local_state({"pressure": False})
        time.sleep(5)
        
        print("\nAudio streaming test complete.")
        
    except KeyboardInterrupt:
        print("Test interrupted by user")
    finally:
        audio_streamer.stop()


# Run test if executed directly
if __name__ == "__main__":
    test_audio_streamer()
