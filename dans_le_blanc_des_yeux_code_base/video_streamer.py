"""
Video streaming module for the Dans le Blanc des Yeux installation.
Handles sending and receiving video streams between devices using custom UDP streaming.

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
    """Handles video streaming between devices."""
    
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
        self.jpeg_quality = 30  # 0-100, higher is better quality
        
        # Frame handling
        self.buffer_size = 65536
        
        # Callbacks
        self.on_internal_frame_received = None
        self.on_external_frame_received = None
        
        # Register as observer for state changes
        system_state.add_observer(self._on_state_change)
        
        print(f"Video streamer initialized with remote IP: {remote_ip}")
    
    def start(self) -> bool:
        """Start the video streaming system."""
        print("Starting video streamer...")
        self.running = True
        
        # Start receiver threads
        self._start_receiver_threads()
        
        # Check initial state to see if we need to start streaming right away
        self._update_streaming_based_on_state()
        
        print("Video streamer started")
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
        if changed_state in ["local", "remote"]:
            self._update_streaming_based_on_state()
    
    def _update_streaming_based_on_state(self) -> None:
        """Update streaming state based on the current system state."""
        local_state = system_state.get_local_state()
        remote_state = system_state.get_remote_state()
        
        # Only proceed if remote is connected
        if not remote_state.get("connected", False):
            self._stop_all_streams()
            return
        
        # Case 1: Both have pressure - stream internal cameras
        if local_state.get("pressure", False) and remote_state.get("pressure", False):
            self._start_internal_stream()
            self._stop_external_stream()
        
        # Case 2: Remote has pressure but local doesn't - stream our external camera
        elif remote_state.get("pressure", False) and not local_state.get("pressure", False):
            self._start_external_stream()
            self._stop_internal_stream()
        
        # Case 3: No streaming needed (local has pressure but remote doesn't, or neither has pressure)
        else:
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
        """Create a UDP socket for receiving video stream."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
        sock.settimeout(0.5)  # Set a timeout for responsive shutdown
        return sock
    
    def _internal_receiver_loop(self) -> None:
        """Receive internal camera stream from remote device."""
        print(f"Starting internal camera receiver on port {INTERNAL_STREAM_PORT}")
        sock = self._create_udp_socket(INTERNAL_STREAM_PORT)
        
        try:
            while self.running:
                try:
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
                except socket.timeout:
                    # This is expected due to the socket timeout
                    pass
                except Exception as e:
                    if self.running:
                        print(f"Error in internal receiver: {e}")
                        time.sleep(1.0)
        finally:
            sock.close()
            print("Internal camera receiver stopped")
    
    def _external_receiver_loop(self) -> None:
        """Receive external camera stream from remote device."""
        print(f"Starting external camera receiver on port {EXTERNAL_STREAM_PORT}")
        sock = self._create_udp_socket(EXTERNAL_STREAM_PORT)
        
        try:
            while self.running:
                try:
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
                except socket.timeout:
                    # This is expected due to the socket timeout
                    pass
                except Exception as e:
                    if self.running:
                        print(f"Error in external receiver: {e}")
                        time.sleep(1.0)
        finally:
            sock.close()
            print("External camera receiver stopped")
    
    def _internal_sender_loop(self) -> None:
        """Send internal camera frames to remote device."""
        print(f"Starting internal camera sender to {self.remote_ip}:{INTERNAL_STREAM_PORT}")
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        try:
            while self.running and self.internal_sending:
                try:
                    # Get latest frame from internal camera
                    frame = self.camera_manager.get_internal_frame()
                    
                    if frame is not None:
                        # Encode frame as jpeg
                        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]
                        _, jpeg_data = cv2.imencode('.jpg', frame, encode_param)
                        
                        # Get size of jpeg data
                        frame_size = len(jpeg_data)
                        
                        # Create packet with size prefix
                        packet = struct.pack(">I", frame_size) + jpeg_data.tobytes()
                        
                        # Send packet
                        sock.sendto(packet, (self.remote_ip, INTERNAL_STREAM_PORT))
                    
                    # Control frame rate
                    time.sleep(0.033)  # ~30 fps
                except Exception as e:
                    if self.running and self.internal_sending:
                        print(f"Error in internal sender: {e}")
                        time.sleep(1.0)
        finally:
            sock.close()
            print("Internal camera sender stopped")
    
    def _external_sender_loop(self) -> None:
        """Send external camera frames to remote device."""
        print(f"Starting external camera sender to {self.remote_ip}:{EXTERNAL_STREAM_PORT}")
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        try:
            while self.running and self.external_sending:
                try:
                    # Get latest frame from external camera
                    frame = self.camera_manager.get_external_frame()
                    
                    if frame is not None:
                        # Encode frame as jpeg
                        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]
                        _, jpeg_data = cv2.imencode('.jpg', frame, encode_param)
                        
                        # Get size of jpeg data
                        frame_size = len(jpeg_data)
                        
                        # Create packet with size prefix
                        packet = struct.pack(">I", frame_size) + jpeg_data.tobytes()
                        
                        # Send packet
                        sock.sendto(packet, (self.remote_ip, EXTERNAL_STREAM_PORT))
                    
                    # Control frame rate
                    time.sleep(0.033)  # ~30 fps
                except Exception as e:
                    if self.running and self.external_sending:
                        print(f"Error in external sender: {e}")
                        time.sleep(1.0)
        finally:
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
