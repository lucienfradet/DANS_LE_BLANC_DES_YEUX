"""
Improved video streaming module for the Dans le Blanc des Yeux installation.
Features more robust error handling, dynamic quality adjustment, and improved logging.

Streaming Logic:
1. When remote device has pressure=true and local doesn't: Send external PiCamera feed
2. When local device has pressure=true and remote doesn't: Receive remote's external PiCamera feed
3. When both have pressure=true: Send internal camera feed, receive remote's internal camera feed
4. When neither has pressure: No streaming required
"""

import os
import time
import threading
import socket
import struct
import numpy as np
import cv2
from typing import Dict, Optional, Tuple, List, Callable

from system_state import system_state
from camera_manager import CameraManager

# Port configuration
INTERNAL_STREAM_PORT = 5000  # Port for internal camera stream
EXTERNAL_STREAM_PORT = 5001  # Port for external camera stream

class VideoStreamer:
    """Handles video streaming between devices with improved reliability."""
    
    def __init__(self, camera_manager: CameraManager, remote_ip: str):
        self.camera_manager = camera_manager
        self.remote_ip = remote_ip
        
        # Streaming state
        self.internal_sending = False
        self.external_sending = False
        
        # Latest received frames
        self.received_internal_frame = None
        self.received_external_frame = None
        
        # Threading
        self.running = False
        self.threads = []
        self.lock = threading.Lock()
        
        # Frame dimensions and quality
        self.frame_width = 640
        self.frame_height = 480
        self.jpeg_quality = 80  # 0-100, higher is better quality
        
        # Dynamic quality adjustment
        self.adaptive_quality = True
        self.min_quality = 50
        self.target_frame_size = 60000  # Target ~60KB per frame
        self.quality_adjust_interval = 30  # Frames between quality adjustments
        
        # Frame handling
        self.buffer_size = 65536  # UDP buffer size
        
        # Socket reuse configuration
        self.socket_reuse = True
        
        # Performance tracking
        self.frames_sent = 0
        self.frames_received = 0
        self.last_performance_log = time.time()
        self.performance_log_interval = 30.0  # seconds
        
        # Error tracking
        self.consecutive_errors = 0
        self.max_consecutive_errors = 10
        self.last_error_time = 0
        self.error_throttle_interval = 5.0  # seconds
        
        # Health monitoring
        self.health_check_interval = 10.0  # seconds
        self.last_health_check = time.time()
        
        # Callbacks
        self.on_internal_frame_received = None
        self.on_external_frame_received = None
        
        # Register as observer for state changes
        system_state.add_observer(self._on_state_change)
        
        print(f"Video streamer initialized with remote IP: {remote_ip}")
        print(f"Frame dimensions: {self.frame_width}x{self.frame_height}, Initial quality: {self.jpeg_quality}")
        if self.adaptive_quality:
            print(f"Adaptive quality enabled: target frame size {self.target_frame_size/1000:.1f}KB, min quality {self.min_quality}")
    
    def start(self) -> bool:
        """Start the video streaming system with improved error handling."""
        print("Starting video streamer...")
        self.running = True
        
        # Start receiver threads
        self._start_receiver_threads()
        
        # Start health monitoring thread
        health_thread = threading.Thread(target=self._health_monitor_loop)
        health_thread.daemon = True
        health_thread.start()
        self.threads.append(health_thread)
        
        # Check initial state to see if we need to start streaming right away
        self._update_streaming_based_on_state()
        
        print("Video streamer started successfully")
        return True
    
    def stop(self) -> None:
        """Stop all streaming and release resources."""
        print("Stopping video streamer...")
        self.running = False
        
        # Stop any active streaming
        self._stop_all_streams()
        
        # Wait for threads to finish
        for thread in self.threads:
            thread.join(timeout=1.0)
        
        print("Video streamer stopped")
    
    def get_received_internal_frame(self) -> Optional[np.ndarray]:
        """Get the latest received internal camera frame from the remote device."""
        with self.lock:
            return self.received_internal_frame.copy() if self.received_internal_frame is not None else None
    
    def get_received_external_frame(self) -> Optional[np.ndarray]:
        """Get the latest received external camera frame from the remote device."""
        with self.lock:
            return self.received_external_frame.copy() if self.received_external_frame is not None else None
    
    def register_internal_frame_callback(self, callback: Callable[[np.ndarray], None]) -> None:
        """Register a callback for when a new internal frame is received."""
        self.on_internal_frame_received = callback
    
    def register_external_frame_callback(self, callback: Callable[[np.ndarray], None]) -> None:
        """Register a callback for when a new external frame is received."""
        self.on_external_frame_received = callback
    
    def _on_state_change(self, changed_state: str) -> None:
        """Handle system state changes."""
        if changed_state in ["local", "remote", "connection"]:
            self._update_streaming_based_on_state()
    
    def _update_streaming_based_on_state(self) -> None:
        """Update streaming state based on the current system state."""
        local_state = system_state.get_local_state()
        remote_state = system_state.get_remote_state()
        
        # Only proceed if remote is connected
        if not remote_state.get("connected", False):
            self._stop_all_streams()
            print("Stopped streaming: Remote device not connected")
            return
        
        # Case 1: Both have pressure - stream internal cameras
        if local_state.get("pressure", False) and remote_state.get("pressure", False):
            print("Both devices have pressure - streaming internal camera")
            self._start_internal_stream()
            self._stop_external_stream()
        
        # Case 2: Remote has pressure but local doesn't - stream our external camera
        elif remote_state.get("pressure", False) and not local_state.get("pressure", False):
            print("Remote has pressure - streaming our external camera")
            self._start_external_stream()
            self._stop_internal_stream()
        
        # Case 3: No streaming needed (local has pressure but remote doesn't, or neither has pressure)
        else:
            reason = "Local has pressure" if local_state.get("pressure", False) else "Neither has pressure"
            print(f"Stopped streaming: {reason}")
            self._stop_all_streams()
    
    def _start_receiver_threads(self) -> None:
        """Start threads to receive video streams."""
        # Start internal camera receiver thread
        internal_receiver = threading.Thread(target=self._internal_receiver_loop)
        internal_receiver.daemon = True
        internal_receiver.start()
        self.threads.append(internal_receiver)
        
        # Start external camera receiver thread
        external_receiver = threading.Thread(target=self._external_receiver_loop)
        external_receiver.daemon = True
        external_receiver.start()
        self.threads.append(external_receiver)
        
        print("Receiver threads started successfully")
    
    def _health_monitor_loop(self) -> None:
        """Monitor the health of the video streaming system."""
        print("Video streamer health monitor started")
        
        while self.running:
            try:
                current_time = time.time()
                
                # Log performance periodically
                if current_time - self.last_performance_log >= self.performance_log_interval:
                    self._log_performance()
                    self.last_performance_log = current_time
                
                # Check for stream health
                if current_time - self.last_health_check >= self.health_check_interval:
                    self._check_stream_health()
                    self.last_health_check = current_time
                
                # Sleep to avoid tight loop
                time.sleep(1.0)
            except Exception as e:
                print(f"Error in health monitor: {e}")
                time.sleep(5.0)  # Sleep longer on error
    
    def _log_performance(self) -> None:
        """Log streaming performance metrics."""
        elapsed = time.time() - self.last_performance_log
        if elapsed <= 0:
            return
            
        int_fps = self.frames_received / elapsed if elapsed > 0 else 0
        ext_fps = self.frames_sent / elapsed if elapsed > 0 else 0
        
        # Get current streaming state
        local_state = system_state.get_local_state()
        remote_state = system_state.get_remote_state()
        
        stream_state = "None"
        if local_state.get("pressure", False) and remote_state.get("pressure", False):
            stream_state = "Internal"
        elif remote_state.get("pressure", False) and not local_state.get("pressure", False):
            stream_state = "External"
        
        # Log metrics
        print(f"Video Performance: Streaming={stream_state}, "
              f"Frames Received={self.frames_received:.0f} ({int_fps:.1f} fps), "
              f"Frames Sent={self.frames_sent:.0f} ({ext_fps:.1f} fps), "
              f"Quality={self.jpeg_quality}")
        
        # Reset counters
        self.frames_received = 0
        self.frames_sent = 0
    
    def _check_stream_health(self) -> None:
        """Check if streams are healthy and restart if needed."""
        local_state = system_state.get_local_state()
        remote_state = system_state.get_remote_state()
        
        # Check if we're supposed to be streaming internal camera
        if self.internal_sending and not remote_state.get("connected", False):
            print("Health check: Remote disconnected, stopping internal stream")
            self._stop_internal_stream()
        
        # Check if we're supposed to be streaming external camera
        if self.external_sending and not remote_state.get("connected", False):
            print("Health check: Remote disconnected, stopping external stream")
            self._stop_external_stream()
        
        # Verify stream state matches pressure state
        if local_state.get("pressure", False) and remote_state.get("pressure", False):
            if not self.internal_sending:
                print("Health check: Both have pressure but internal stream not active, restarting")
                self._start_internal_stream()
        elif remote_state.get("pressure", False) and not local_state.get("pressure", False):
            if not self.external_sending:
                print("Health check: Remote has pressure but external stream not active, restarting")
                self._start_external_stream()
    
    def _start_internal_stream(self) -> bool:
        """Start streaming the internal camera to the remote device."""
        if self.internal_sending:
            return True
        
        if not self.camera_manager.is_internal_camera_available():
            print("Internal camera not available")
            return False
        
        try:
            # Start sender thread
            sender_thread = threading.Thread(target=self._internal_sender_loop)
            sender_thread.daemon = True
            sender_thread.start()
            self.threads.append(sender_thread)
            
            self.internal_sending = True
            print(f"Started internal camera stream to {self.remote_ip}:{INTERNAL_STREAM_PORT}")
            return True
        except Exception as e:
            print(f"Failed to start internal camera stream: {e}")
            return False
    
    def _start_external_stream(self) -> bool:
        """Start streaming the external camera to the remote device."""
        if self.external_sending:
            return True
        
        if not self.camera_manager.is_external_camera_available():
            print("External camera not available")
            return False
        
        try:
            # Start sender thread
            sender_thread = threading.Thread(target=self._external_sender_loop)
            sender_thread.daemon = True
            sender_thread.start()
            self.threads.append(sender_thread)
            
            self.external_sending = True
            print(f"Started external camera stream to {self.remote_ip}:{EXTERNAL_STREAM_PORT}")
            return True
        except Exception as e:
            print(f"Failed to start external camera stream: {e}")
            return False
    
    def _stop_internal_stream(self) -> None:
        """Stop streaming the internal camera."""
        if self.internal_sending:
            self.internal_sending = False
            print("Stopped internal camera stream")
    
    def _stop_external_stream(self) -> None:
        """Stop streaming the external camera."""
        if self.external_sending:
            self.external_sending = False
            print("Stopped external camera stream")
    
    def _stop_all_streams(self) -> None:
        """Stop all active streams."""
        self._stop_internal_stream()
        self._stop_external_stream()
        print("All streams stopped")
    
    def _create_udp_socket(self, port: int) -> socket.socket:
        """Create a UDP socket for receiving video stream with improved error handling."""
        retries = 3
        for attempt in range(retries):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                
                # Enable address reuse
                if self.socket_reuse:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                
                # Set larger buffer size for better performance
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4*self.buffer_size)
                
                try:
                    sock.bind(("0.0.0.0", port))
                except OSError as e:
                    if "Address already in use" in str(e):
                        print(f"Port {port} already in use. Trying to force close...")
                        # Force close the socket in TIME_WAIT state on Linux
                        if hasattr(socket, 'SO_REUSEPORT'):
                            new_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                            new_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                            new_sock.bind(("0.0.0.0", port))
                            sock = new_sock
                        else:
                            raise
                
                sock.settimeout(0.5)  # Set a timeout for responsive shutdown
                print(f"Created UDP socket on port {port} (attempt {attempt+1})")
                return sock
            except Exception as e:
                if attempt < retries - 1:
                    print(f"Error creating socket: {e}, retrying...")
                    time.sleep(1.0)
                else:
                    print(f"Failed to create socket after {retries} attempts: {e}")
                    raise
        
        # Should never reach here due to raise above
        raise RuntimeError("Failed to create UDP socket")
    
    def _adjust_quality(self, frame_size: int) -> None:
        """Dynamically adjust JPEG quality based on frame size."""
        if not self.adaptive_quality:
            return
            
        target_ratio = frame_size / self.target_frame_size
        
        if target_ratio > 1.2:  # Frame too large
            # Reduce quality more aggressively when far from target
            adjustment = -5 if target_ratio > 2.0 else -2
            self.jpeg_quality = max(self.min_quality, self.jpeg_quality + adjustment)
        elif target_ratio < 0.8:  # Frame too small
            # Increase quality more conservatively
            self.jpeg_quality = min(95, self.jpeg_quality + 1)
    
    def _internal_receiver_loop(self) -> None:
        """Receive internal camera stream from remote device with improved error handling."""
        print(f"Starting internal camera receiver on port {INTERNAL_STREAM_PORT}")
        
        sock = None
        retry_count = 0
        
        while self.running:
            try:
                # Create socket if it doesn't exist
                if sock is None:
                    sock = self._create_udp_socket(INTERNAL_STREAM_PORT)
                    retry_count = 0
                
                # Receive packet with size prefix
                data, addr = sock.recvfrom(self.buffer_size)
                
                if len(data) < 4:  # Need at least 4 bytes for size
                    continue
                
                # First 4 bytes contain the size of the jpeg data
                frame_size = struct.unpack(">I", data[:4])[0]
                
                # Rest of the packet contains the jpeg data
                jpeg_data = data[4:]
                
                # If we didn't get the full frame, we'll ignore this packet
                if len(jpeg_data) != frame_size:
                    continue
                
                # Decode jpeg data to opencv format
                frame = cv2.imdecode(np.frombuffer(jpeg_data, dtype=np.uint8), cv2.IMREAD_COLOR)
                
                if frame is not None:
                    with self.lock:
                        self.received_internal_frame = frame
                    
                    # Call callback if registered
                    if self.on_internal_frame_received:
                        self.on_internal_frame_received(frame)
                    
                    # Update statistics
                    self.frames_received += 1
                    self.consecutive_errors = 0
            except socket.timeout:
                # This is expected due to the socket timeout
                pass
            except Exception as e:
                current_time = time.time()
                self.consecutive_errors += 1
                
                # Only log frequent errors if not intentionally stopping
                if self.running and (current_time - self.last_error_time > self.error_throttle_interval):
                    print(f"Error in internal receiver: {e}")
                    self.last_error_time = current_time
                
                # If we have persistent errors, recreate the socket
                if self.consecutive_errors >= self.max_consecutive_errors:
                    print(f"Too many consecutive errors ({self.consecutive_errors}), recreating socket")
                    try:
                        if sock:
                            sock.close()
                    except:
                        pass
                    sock = None
                    retry_count += 1
                    
                    # Add increasing delay between retries
                    time.sleep(min(1.0 * (2 ** retry_count), 10.0))
                else:
                    time.sleep(0.1)
        
        # Clean up socket
        if sock:
            try:
                sock.close()
            except:
                pass
        
        print("Internal camera receiver stopped")
    
    def _external_receiver_loop(self) -> None:
        """Receive external camera stream from remote device with improved error handling."""
        print(f"Starting external camera receiver on port {EXTERNAL_STREAM_PORT}")
        
        sock = None
        retry_count = 0
        
        while self.running:
            try:
                # Create socket if it doesn't exist
                if sock is None:
                    sock = self._create_udp_socket(EXTERNAL_STREAM_PORT)
                    retry_count = 0
                
                # Receive packet with size prefix
                data, addr = sock.recvfrom(self.buffer_size)
                
                if len(data) < 4:  # Need at least 4 bytes for size
                    continue
                
                # First 4 bytes contain the size of the jpeg data
                frame_size = struct.unpack(">I", data[:4])[0]
                
                # Rest of the packet contains the jpeg data
                jpeg_data = data[4:]
                
                # If we didn't get the full frame, we'll ignore this packet
                if len(jpeg_data) != frame_size:
                    continue
                
                # Decode jpeg data to opencv format
                frame = cv2.imdecode(np.frombuffer(jpeg_data, dtype=np.uint8), cv2.IMREAD_COLOR)
                
                if frame is not None:
                    with self.lock:
                        self.received_external_frame = frame
                    
                    # Call callback if registered
                    if self.on_external_frame_received:
                        self.on_external_frame_received(frame)
                    
                    # Update statistics
                    self.frames_received += 1
                    self.consecutive_errors = 0
            except socket.timeout:
                # This is expected due to the socket timeout
                pass
            except Exception as e:
                current_time = time.time()
                self.consecutive_errors += 1
                
                # Only log frequent errors if not intentionally stopping
                if self.running and (current_time - self.last_error_time > self.error_throttle_interval):
                    print(f"Error in external receiver: {e}")
                    self.last_error_time = current_time
                
                # If we have persistent errors, recreate the socket
                if self.consecutive_errors >= self.max_consecutive_errors:
                    print(f"Too many consecutive errors ({self.consecutive_errors}), recreating socket")
                    try:
                        if sock:
                            sock.close()
                    except:
                        pass
                    sock = None
                    retry_count += 1
                    
                    # Add increasing delay between retries
                    time.sleep(min(1.0 * (2 ** retry_count), 10.0))
                else:
                    time.sleep(0.1)
        
        # Clean up socket
        if sock:
            try:
                sock.close()
            except:
                pass
        
        print("External camera receiver stopped")
    
    def _internal_sender_loop(self) -> None:
        """Send internal camera frames to remote device with improved error handling."""
        print(f"Starting internal camera sender to {self.remote_ip}:{INTERNAL_STREAM_PORT}")
        
        sock = None
        frame_count = 0
        
        try:
            # Create UDP socket for sending
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4*self.buffer_size)
            
            while self.running and self.internal_sending:
                try:
                    # Get latest frame from internal camera
                    frame = self.camera_manager.get_internal_frame()
                    
                    if frame is not None:
                        # Resize if too large (to minimize UDP fragmentation)
                        if frame.shape[1] > self.frame_width or frame.shape[0] > self.frame_height:
                            frame = cv2.resize(frame, (self.frame_width, self.frame_height))
                        
                        # Encode frame as jpeg
                        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]
                        _, jpeg_data = cv2.imencode('.jpg', frame, encode_param)
                        
                        # Adjust quality if needed
                        frame_count += 1
                        if self.adaptive_quality and frame_count % self.quality_adjust_interval == 0:
                            self._adjust_quality(len(jpeg_data))
                        
                        # Get size of jpeg data
                        frame_size = len(jpeg_data)
                        
                        # Create packet with size prefix
                        packet = struct.pack(">I", frame_size) + jpeg_data.tobytes()
                        
                        # Send packet
                        sock.sendto(packet, (self.remote_ip, INTERNAL_STREAM_PORT))
                        
                        # Update statistics
                        self.frames_sent += 1
                    
                    # Control frame rate (dynamically adjust based on frame size)
                    frame_delay = 0.033  # ~30 fps base rate
                    if frame is not None and frame_size > self.target_frame_size * 1.5:
                        # Slow down if frames are too large
                        frame_delay = 0.05  # ~20 fps
                    
                    time.sleep(frame_delay)
                except Exception as e:
                    if self.running and self.internal_sending:
                        # Only log errors periodically to avoid flooding
                        current_time = time.time()
                        if current_time - self.last_error_time > self.error_throttle_interval:
                            print(f"Error in internal sender: {e}")
                            self.last_error_time = current_time
                        time.sleep(0.5)
        except Exception as e:
            print(f"Fatal error in internal sender: {e}")
        finally:
            if sock:
                sock.close()
            print("Internal camera sender stopped")
    
    def _external_sender_loop(self) -> None:
        """Send external camera frames to remote device with improved error handling."""
        print(f"Starting external camera sender to {self.remote_ip}:{EXTERNAL_STREAM_PORT}")
        
        sock = None
        frame_count = 0
        
        try:
            # Create UDP socket for sending
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4*self.buffer_size)
            
            while self.running and self.external_sending:
                try:
                    # Get latest frame from external camera
                    frame = self.camera_manager.get_external_frame()
                    
                    if frame is not None:
                        # Resize if too large (to minimize UDP fragmentation)
                        if frame.shape[1] > self.frame_width or frame.shape[0] > self.frame_height:
                            frame = cv2.resize(frame, (self.frame_width, self.frame_height))
                        
                        # Encode frame as jpeg
                        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]
                        _, jpeg_data = cv2.imencode('.jpg', frame, encode_param)
                        
                        # Adjust quality if needed
                        frame_count += 1
                        if self.adaptive_quality and frame_count % self.quality_adjust_interval == 0:
                            self._adjust_quality(len(jpeg_data))
                        
                        # Get size of jpeg data
                        frame_size = len(jpeg_data)
                        
                        # Create packet with size prefix
                        packet = struct.pack(">I", frame_size) + jpeg_data.tobytes()
                        
                        # Send packet
                        sock.sendto(packet, (self.remote_ip, EXTERNAL_STREAM_PORT))
                        
                        # Update statistics
                        self.frames_sent += 1
                    
                    # Control frame rate (dynamically adjust based on frame size)
                    frame_delay = 0.033  # ~30 fps base rate
                    if frame is not None and frame_size > self.target_frame_size * 1.5:
                        # Slow down if frames are too large
                        frame_delay = 0.05  # ~20 fps
                    
                    time.sleep(frame_delay)
                except Exception as e:
                    if self.running and self.external_sending:
                        # Only log errors periodically to avoid flooding
                        current_time = time.time()
                        if current_time - self.last_error_time > self.error_throttle_interval:
                            print(f"Error in external sender: {e}")
                            self.last_error_time = current_time
                        time.sleep(0.5)
        except Exception as e:
            print(f"Fatal error in external sender: {e}")
        finally:
            if sock:
                sock.close()
            print("External camera sender stopped")


# Test function to run the video streamer standalone
def test_video_streamer():
    """Test the video streamer by displaying sent and received frames."""
    from camera_manager import CameraManager
    import cv2
    
    # Set up system state for testing
    system_state.update_local_state({"pressure": False})
    system_state.update_remote_state({"pressure": True, "connected": True})
    
    # Initialize camera manager
    camera_manager = CameraManager()
    if not camera_manager.start():
        print("Failed to start camera manager")
        return
    
    # Initialize video streamer with loopback address for testing
    video_streamer = VideoStreamer(camera_manager, "127.0.0.1")
    video_streamer.start()
    
    try:
        # Create display window
        cv2.namedWindow("Test Display", cv2.WINDOW_NORMAL)
        
        print("\nTesting different pressure states:")
        print("\n1. Remote pressure, local no pressure - Should stream our external camera")
        time.sleep(5)
        
        print("\n2. Both have pressure - Should stream our internal camera")
        system_state.update_local_state({"pressure": True})
        time.sleep(5)
        
        print("\n3. Local pressure, remote no pressure - Should stop streaming")
        system_state.update_remote_state({"pressure": False})
        time.sleep(5)
        
        print("\n4. Neither has pressure - Should stop streaming")
        system_state.update_local_state({"pressure": False})
        
        print("\nStreaming test complete. Press any key to exit.")
        while True:
            # Display the frames we're receiving (for testing)
            internal_frame = video_streamer.get_received_internal_frame()
            external_frame = video_streamer.get_received_external_frame()
            
            display_frame = None
            if internal_frame is not None:
                display_frame = internal_frame
                cv2.putText(display_frame, "Received Internal", (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            elif external_frame is not None:
                display_frame = external_frame
                cv2.putText(display_frame, "Received External", (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                
            if display_frame is not None:
                cv2.imshow("Test Display", display_frame)
            
            # Exit on key press
            if cv2.waitKey(30) >= 0:
                break
            
            time.sleep(0.033)  # ~30fps
    except KeyboardInterrupt:
        print("Test interrupted by user")
    finally:
        # Clean up
        cv2.destroyAllWindows()
        video_streamer.stop()
        camera_manager.stop()


# Run test if executed directly
if __name__ == "__main__":
    test_video_streamer()
