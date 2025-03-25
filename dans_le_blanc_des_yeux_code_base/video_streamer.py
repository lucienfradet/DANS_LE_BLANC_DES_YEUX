"""
Video streaming module for the Dans le Blanc des Yeux installation.
Handles sending and receiving video streams between devices using GStreamer.
"""

import os
import time
import threading
import subprocess
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
        self.sender_processes = []
        self.lock = threading.Lock()
        
        # Frame dimensions and quality
        self.frame_width = 640
        self.frame_height = 480
        self.jpeg_quality = 80  # 0-100, higher is better quality
        
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
        
        # Start internal camera stream by default (always active)
        self._start_internal_stream()
        
        print("Video streamer started")
        return True
    
    def stop(self) -> None:
        """Stop all streaming and release resources."""
        print("Stopping video streamer...")
        self.running = False
        
        # Stop any active GStreamer processes
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
            self._handle_pressure_state_change()
    
    def _handle_pressure_state_change(self) -> None:
        """Handle changes in pressure state to control external camera streaming."""
        local_state = system_state.get_local_state()
        remote_state = system_state.get_remote_state()
        
        # If remote has pressure and we don't, start streaming our external camera
        if remote_state["pressure"] and not local_state["pressure"] and remote_state["connected"]:
            if not self.external_sending:
                print("Remote has pressure, starting external camera stream")
                self._start_external_stream()
        else:
            # Otherwise, stop the external stream if it's running
            if self.external_sending:
                print("Stopping external camera stream")
                self._stop_external_stream()
    
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
            print("Internal camera already streaming")
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
            print("External camera already streaming")
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
        self.internal_sending = False
        print("Stopped internal camera stream")
    
    def _stop_external_stream(self) -> None:
        """Stop streaming the external camera."""
        self.external_sending = False
        print("Stopped external camera stream")
    
    def _stop_all_streams(self) -> None:
        """Stop all active streams."""
        self.internal_sending = False
        self.external_sending = False
        
        # Terminate any GStreamer processes
        for process in self.sender_processes:
            try:
                process.terminate()
                process.wait(timeout=1.0)
            except Exception:
                pass
        
        self.sender_processes = []
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
    
    # Set up dummy system state for testing
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
        while True:
            # Get received frames
            internal_frame = video_streamer.get_received_internal_frame()
            external_frame = video_streamer.get_received_external_frame()
            
            # Display received internal frame
            if internal_frame is not None:
                cv2.imshow("Received Internal Camera", internal_frame)
            
            # Display received external frame
            if external_frame is not None:
                cv2.imshow("Received External Camera", external_frame)
            
            # Exit on 'q' key
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    except KeyboardInterrupt:
        print("Test interrupted by user")
    finally:
        # Clean up
        video_streamer.stop()
        camera_manager.stop()
        cv2.destroyAllWindows()


# Run test if executed directly
if __name__ == "__main__":
    test_video_streamer()
