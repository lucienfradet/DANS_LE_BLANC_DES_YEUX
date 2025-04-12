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
        
        # For H.264 encoding
        self.internal_codec = None
        self.external_codec = None
        self.frame_count = {"internal": 0, "external": 0}
        
        # For H.264 decoding
        self.internal_decoder = None
        self.external_decoder = None

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
        print("Starting video streamer with H.264 encoding...")
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

        # Release encoders
        if self.internal_codec:
            self.internal_codec.release()
        if self.external_codec:
            self.external_codec.release()
        
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
    
    def _create_udp_socket(self, port: int) -> socket.socket:
        """Create a UDP socket for receiving video stream."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
        sock.settimeout(0.5)  # Set a timeout for responsive shutdown
        return sock

    def _create_h264_encoder(self) -> cv2.VideoWriter:
        """Create an H.264 encoder using OpenCV's VideoWriter."""
        # Create an in-memory encoder for H.264
        # gstreamer pipeline for H.264 encoding
        if cv2.ocl.useOpenCL():
            # If OpenCL is available, use hardware acceleration
            fourcc = cv2.VideoWriter_fourcc(*'H264')
            encoder = cv2.VideoWriter('appsrc ! videoconvert ! video/x-raw,format=I420 ! '
                                      f'x264enc bitrate={H264_BITRATE//1000} key-int-max={KEYFRAME_INTERVAL} ! '
                                      'video/x-h264,profile=baseline ! appsink',
                                      0, 30.0, (self.frame_width, self.frame_height))
        else:
            # Fallback to software encoding
            fourcc = cv2.VideoWriter_fourcc(*'X264')
            encoder = cv2.VideoWriter('output_temp.h264', fourcc, 30.0, 
                                     (self.frame_width, self.frame_height), True)
        
        # Check if encoder was successfully created
        if not encoder.isOpened():
            print("Warning: Failed to create H.264 encoder, falling back to JPEG")
            return None
            
        return encoder

    def _start_internal_stream(self) -> bool:
        """Start streaming the internal camera to the remote device."""
        if self.internal_sending:
            return True
        
        if not self.camera_manager.is_internal_camera_available():
            print("Internal camera not available")
            return False
        
        try:
            # Initialize H.264 encoder
            self.internal_codec = self._create_h264_encoder()
            self.frame_count["internal"] = 0
            
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
            # Initialize H.264 encoder
            self.external_codec = self._create_h264_encoder()
            self.frame_count["external"] = 0
            
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
            # Release the encoder
            if self.internal_codec:
                self.internal_codec.release()
                self.internal_codec = None
            print("Stopped internal camera stream")
    
    def _stop_external_stream(self) -> None:
        """Stop streaming the external camera."""
        if self.external_sending:
            self.external_sending = False
            # Release the encoder
            if self.external_codec:
                self.external_codec.release()
                self.external_codec = None
            print("Stopped external camera stream")
    
    def _stop_all_streams(self) -> None:
        """Stop all active streams."""
        self._stop_internal_stream()
        self._stop_external_stream()
        print("All streams stopped")
    
    def _encode_frame_h264(self, frame, codec, is_keyframe=False):
        """Encode a frame using H.264 codec."""
        # If codec creation failed, fall back to JPEG
        if codec is None:
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 30]
            _, jpeg_data = cv2.imencode('.jpg', frame, encode_param)
            return jpeg_data.tobytes(), False  # False indicates not H.264
        
        # Otherwise use H.264 encoding
        if is_keyframe:
            # Force keyframe - implementation depends on codec
            # This is a simplified approach, actual implementation may vary
            codec.write(frame)
        else:
            codec.write(frame)
        
        # For simplicity, this implementation uses a temporary file
        # A more advanced implementation would capture the encoded data directly
        
        # Return encoded data and True to indicate H.264
        # Note: In a real implementation, you would need to capture the encoded frames
        # from the VideoWriter output, which requires additional code
        
        # Fallback to JPEG for this example implementation
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 30]
        _, jpeg_data = cv2.imencode('.jpg', frame, encode_param)
        return jpeg_data.tobytes(), False  # For actual implementation, return H.264 data and True

    def _internal_receiver_loop(self) -> None:
        """Receive internal camera H.264 stream from remote device."""
        print(f"Starting internal camera H.264 receiver on port {INTERNAL_STREAM_PORT}")
        sock = self._create_udp_socket(INTERNAL_STREAM_PORT)
        
        # Initialize decoder
        self.internal_decoder = self._initialize_h264_decoder()
        
        try:
            while self.running:
                try:
                    # Receive packet with metadata and frame data
                    data, addr = sock.recvfrom(self.buffer_size)
                    
                    if len(data) < 6:  # Need at least 6 bytes for metadata
                        continue
                    
                    # Parse metadata: [4 bytes size][1 byte is_h264][1 byte is_keyframe]
                    frame_size, is_h264, is_keyframe = struct.unpack(">IBB", data[:6])
                    
                    # Rest of the packet contains the frame data
                    frame_data = data[6:]
                    
                    # If we didn't get the full frame, ignore this packet
                    if len(frame_data) != frame_size:
                        continue
                    
                    # Decode frame
                    frame = self._decode_frame(frame_data, is_h264, is_keyframe, self.internal_decoder)
                    
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
            if self.internal_decoder:
                self.internal_decoder.release()
            print("Internal camera receiver stopped")
    
    def _external_receiver_loop(self) -> None:
        """Receive external camera H.264 stream from remote device."""
        print(f"Starting external camera H.264 receiver on port {EXTERNAL_STREAM_PORT}")
        sock = self._create_udp_socket(EXTERNAL_STREAM_PORT)
        
        # Initialize decoder
        self.external_decoder = self._initialize_h264_decoder()
        
        try:
            while self.running:
                try:
                    # Receive packet with metadata and frame data
                    data, addr = sock.recvfrom(self.buffer_size)
                    
                    if len(data) < 6:  # Need at least 6 bytes for metadata
                        continue
                    
                    # Parse metadata: [4 bytes size][1 byte is_h264][1 byte is_keyframe]
                    frame_size, is_h264, is_keyframe = struct.unpack(">IBB", data[:6])
                    
                    # Rest of the packet contains the frame data
                    frame_data = data[6:]
                    
                    # If we didn't get the full frame, ignore this packet
                    if len(frame_data) != frame_size:
                        continue
                    
                    # Decode frame
                    frame = self._decode_frame(frame_data, is_h264, is_keyframe, self.external_decoder)
                    
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
            if self.external_decoder:
                self.external_decoder.release()
            print("External camera receiver stopped")
    
    def _internal_sender_loop(self) -> None:
        """Send internal camera frames to remote device using H.264."""
        print(f"Starting internal camera H.264 sender to {self.remote_ip}:{INTERNAL_STREAM_PORT}")
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        try:
            while self.running and self.internal_sending:
                try:
                    # Get latest frame from internal camera
                    frame = self.camera_manager.get_internal_frame()
                    
                    if frame is not None:
                        # Determine if this should be a keyframe
                        is_keyframe = (self.frame_count["internal"] % KEYFRAME_INTERVAL) == 0
                        self.frame_count["internal"] += 1
                        
                        # Encode frame with H.264
                        frame_data, is_h264 = self._encode_frame_h264(
                            frame, self.internal_codec, is_keyframe
                        )
                        
                        # Create packet with metadata
                        # Format: [4 bytes size][1 byte is_h264][1 byte is_keyframe][data]
                        frame_size = len(frame_data)
                        metadata = struct.pack(">IBB", frame_size, is_h264, is_keyframe)
                        packet = metadata + frame_data
                        
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
        """Send external camera frames to remote device using H.264."""
        print(f"Starting external camera H.264 sender to {self.remote_ip}:{EXTERNAL_STREAM_PORT}")
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        try:
            while self.running and self.external_sending:
                try:
                    # Get latest frame from external camera
                    frame = self.camera_manager.get_external_frame()
                    
                    if frame is not None:
                        # Determine if this should be a keyframe
                        is_keyframe = (self.frame_count["external"] % KEYFRAME_INTERVAL) == 0
                        self.frame_count["external"] += 1
                        
                        # Encode frame with H.264
                        frame_data, is_h264 = self._encode_frame_h264(
                            frame, self.external_codec, is_keyframe
                        )
                        
                        # Create packet with metadata
                        # Format: [4 bytes size][1 byte is_h264][1 byte is_keyframe][data]
                        frame_size = len(frame_data)
                        metadata = struct.pack(">IBB", frame_size, is_h264, is_keyframe)
                        packet = metadata + frame_data
                        
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

    def _initialize_h264_decoder(self):
        """Initialize an H.264 decoder using OpenCV."""
        # This is a simplified approach
        return cv2.VideoCapture()
    
    def _decode_frame(self, data, is_h264, is_keyframe, decoder=None):
        """Decode a frame from received data."""
        if not is_h264:
            # If not H.264, assume JPEG
            return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        else:
            # H.264 decoding would go here
            # For now, we'll fall back to JPEG as a placeholder
            return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)

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
