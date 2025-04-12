"""
Video streaming module for the Dans le Blanc des Yeux installation.
Handles sending and receiving video streams between devices using H.265 encoding.

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
from typing import Dict, Optional, Tuple, List, Callable, Any, Union

from system_state import system_state
from camera_manager import CameraManager

# Port configuration
INTERNAL_STREAM_PORT = 5000  # Port for internal camera stream
EXTERNAL_STREAM_PORT = 5001  # Port for external camera stream

# H.265 Encoding Constants
KEYFRAME_INTERVAL = 30     # Send a keyframe every 30 frames
H265_BITRATE = 1000000     # 1 Mbps default bitrate
FRAME_RATE = 30            # Target frame rate

# Encoding protocol constants
CODEC_JPEG = 0  # Still supported for backward compatibility
CODEC_H265 = 1

class VideoStreamer:
    """Handles video streaming between devices using H.265 encoding."""
    
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
        self.jpeg_quality = 30  # For backward compatibility only
        
        # Frame counters for keyframe detection
        self.frame_count = {"internal": 0, "external": 0}
        
        # Frame handling
        self.buffer_size = 65536  # UDP packet buffer size
        
        # Callbacks for new frames
        self.on_internal_frame_received = None
        self.on_external_frame_received = None
        
        # Register as observer for state changes
        system_state.add_observer(self._on_state_change)
        
        # Initialize GStreamer
        import gi
        gi.require_version('Gst', '1.0')
        from gi.repository import Gst
        if not Gst.is_initialized():
            Gst.init(None)
        
        print(f"Video streamer initialized with remote IP: {remote_ip}")
        
        # Always assume GStreamer is available
        self.has_gstreamer = True
    
    def _check_gstreamer_support(self) -> bool:
        """Always return True - we're assuming GStreamer is available."""
        print("Assuming GStreamer is available as requested")
        return True
    
    def start(self) -> bool:
        """Start the video streaming system."""
        print("Starting video streamer with H.265 encoding...")
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
        self._release_encoders()
        
        print("Video streamer stopped")
    
    def _release_encoders(self) -> None:
        """Clean up resources."""
        # We don't need to do anything here since we're using direct GStreamer pipelines
        # that are created and cleaned up for each frame
        pass
    
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

    def _create_h265_encoder(self, width: int, height: int) -> cv2.VideoWriter:
        """Create an H.265 encoder using GStreamer.
        
        This is a placeholder since we're using direct GStreamer encoding.
        The returned encoder is not actually used for encoding, but is kept for API compatibility.
        """
        # Return a dummy encoder
        return True

    def _start_internal_stream(self) -> bool:
        """Start streaming the internal camera to the remote device."""
        if self.internal_sending:
            return True
        
        if not self.camera_manager.is_internal_camera_available():
            print("Internal camera not available")
            return False
        
        try:
            # Get a test frame to determine dimensions
            test_frame = self.camera_manager.get_internal_frame()
            if test_frame is None:
                print("Could not get test frame from internal camera")
                return False
                
            height, width = test_frame.shape[:2]
            self.frame_width, self.frame_height = width, height
            
            # Reset frame counter for keyframe generation
            self.frame_count["internal"] = 0
            
            # Start sender thread
            sender_thread = threading.Thread(target=self._internal_sender_loop)
            sender_thread.daemon = True
            sender_thread.start()
            self.threads.append(sender_thread)
            
            self.internal_sending = True
            print(f"Started internal camera H.265 stream to {self.remote_ip}:{INTERNAL_STREAM_PORT}")
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
            # Get a test frame to determine dimensions
            test_frame = self.camera_manager.get_external_frame()
            if test_frame is None:
                print("Could not get test frame from external camera")
                return False
                
            # Reset frame counter for keyframe generation
            self.frame_count["external"] = 0
            
            # Start sender thread
            sender_thread = threading.Thread(target=self._external_sender_loop)
            sender_thread.daemon = True
            sender_thread.start()
            self.threads.append(sender_thread)
            
            self.external_sending = True
            print(f"Started external camera H.265 stream to {self.remote_ip}:{EXTERNAL_STREAM_PORT}")
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
    
    def _encode_frame(self, frame: np.ndarray, encoder: Any, is_keyframe: bool = False) -> Tuple[bytes, int]:
        """
        Encode a frame using H.265.
        
        Args:
            frame: Input frame to encode
            encoder: H.265 encoder object (not actually used, kept for API compatibility)
            is_keyframe: Whether to force a keyframe
            
        Returns:
            Tuple of (encoded_data, codec_type)
        """
        try:
            # Use GStreamer to encode H.265 directly to memory
            import gi
            gi.require_version('Gst', '1.0')
            from gi.repository import Gst, GLib
            
            # Initialize GStreamer if not already done
            if not Gst.is_initialized():
                Gst.init(None)
            
            # Create a pipeline for encoding a single frame to H.265
            pipeline_str = (
                f"appsrc name=src ! videoconvert ! video/x-raw,format=I420 ! "
                f"x265enc bitrate={H265_BITRATE//1000} "
            )
            
            # Add keyframe forcing if needed
            if is_keyframe:
                pipeline_str += "key-int-max=1 ! "
            else:
                pipeline_str += f"key-int-max={KEYFRAME_INTERVAL} ! "
            
            # Complete the pipeline
            pipeline_str += (
                f"video/x-h265,profile=main ! h265parse ! "
                f"appsink name=sink sync=false"
            )
            
            # Create pipeline
            pipeline = Gst.parse_launch(pipeline_str)
            
            # Get source and sink elements
            src = pipeline.get_by_name("src")
            sink = pipeline.get_by_name("sink")
            
            # Prepare buffer with frame data
            height, width = frame.shape[:2]
            frame_size = width * height * 3  # RGB format
            
            # Convert frame to bytes
            if frame.dtype != np.uint8:
                frame = frame.astype(np.uint8)
            
            # Ensure frame is contiguous
            if not frame.flags['C_CONTIGUOUS']:
                frame = np.ascontiguousarray(frame)
            
            # Create buffer
            buffer = Gst.Buffer.new_allocate(None, frame_size, None)
            buffer.fill(0, frame.tobytes())
            
            # Set buffer timestamp and duration
            buffer.pts = Gst.CLOCK_TIME_NONE
            buffer.dts = Gst.CLOCK_TIME_NONE
            buffer.duration = Gst.CLOCK_TIME_NONE
            
            # Start pipeline
            pipeline.set_state(Gst.State.PLAYING)
            
            # Push buffer to source
            src.emit("push-buffer", buffer)
            src.emit("end-of-stream")
            
            # Get encoded data from sink
            encoded_data = bytearray()
            
            # Pull samples until EOS
            while True:
                sample = sink.try_pull_sample(Gst.SECOND)
                if sample is None:
                    break
                
                buffer = sample.get_buffer()
                
                # Extract data from buffer
                success, mapinfo = buffer.map(Gst.MapFlags.READ)
                if success:
                    encoded_data.extend(mapinfo.data)
                    buffer.unmap(mapinfo)
            
            # Stop pipeline
            pipeline.set_state(Gst.State.NULL)
            
            if len(encoded_data) == 0:
                raise RuntimeError("No H.265 data produced by encoder")
                
            # Return encoded H.265 data
            return bytes(encoded_data), CODEC_H265
            
        except Exception as e:
            print(f"Error encoding with H.265: {e}")
            raise RuntimeError(f"H.265 encoding failed: {e}")


    # Method removed - we're using direct GStreamer decoding in _decode_frame
    
    def _decode_frame(self, data: bytes, codec_type: int, is_keyframe: bool, decoder: Any) -> np.ndarray:
        """
        Decode a frame from received data.
        
        Args:
            data: Encoded frame data
            codec_type: Type of encoding (CODEC_JPEG or CODEC_H265)
            is_keyframe: Whether this is a keyframe
            decoder: Decoder object (not used, kept for API compatibility)
            
        Returns:
            Decoded frame
        
        Raises:
            RuntimeError: If decoding fails
        """
        try:
            if codec_type == CODEC_JPEG:
                # JPEG decoding is straightforward
                frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    raise RuntimeError("Failed to decode JPEG data")
                return frame
            elif codec_type == CODEC_H265:
                # Use GStreamer for H.265 decoding
                import gi
                gi.require_version('Gst', '1.0')
                from gi.repository import Gst, GLib
                
                # Initialize GStreamer if not already done
                if not Gst.is_initialized():
                    Gst.init(None)
                
                # Create pipeline for decoding H.265 data
                pipeline_str = (
                    "appsrc name=src ! "
                    "h265parse ! avdec_h265 ! videoconvert ! "
                    "video/x-raw,format=BGR ! appsink name=sink sync=false"
                )
                
                pipeline = Gst.parse_launch(pipeline_str)
                
                # Get source and sink elements
                src = pipeline.get_by_name("src")
                sink = pipeline.get_by_name("sink")
                
                # Create buffer with H.265 data
                buffer = Gst.Buffer.new_allocate(None, len(data), None)
                buffer.fill(0, data)
                
                # Start pipeline
                pipeline.set_state(Gst.State.PLAYING)
                
                # Push data into pipeline
                src.emit("push-buffer", buffer)
                src.emit("end-of-stream")
                
                # Get the decoded frame
                sample = sink.try_pull_sample(Gst.SECOND)
                if sample:
                    buffer = sample.get_buffer()
                    success, mapinfo = buffer.map(Gst.MapFlags.READ)
                    
                    if success:
                        # Get frame dimensions from caps
                        caps = sample.get_caps()
                        structure = caps.get_structure(0)
                        width = structure.get_value("width")
                        height = structure.get_value("height")
                        
                        # Convert buffer to numpy array
                        frame_data = mapinfo.data
                        frame = np.frombuffer(frame_data, dtype=np.uint8)
                        frame = frame.reshape((height, width, 3))
                        
                        buffer.unmap(mapinfo)
                        
                        # Stop pipeline
                        pipeline.set_state(Gst.State.NULL)
                        
                        return frame
                
                # Stop pipeline if no sample was retrieved
                pipeline.set_state(Gst.State.NULL)
                raise RuntimeError("Failed to decode H.265 data")
            else:
                raise RuntimeError(f"Unknown codec type: {codec_type}")
        except Exception as e:
            raise RuntimeError(f"Error decoding frame: {e}")


    def _internal_receiver_loop(self) -> None:
        """Receive internal camera stream from remote device."""
        print(f"Starting internal camera H.265 receiver on port {INTERNAL_STREAM_PORT}")
        sock = self._create_udp_socket(INTERNAL_STREAM_PORT)
        
        try:
            while self.running:
                try:
                    # Receive packet with metadata and frame data
                    data, addr = sock.recvfrom(self.buffer_size)
                    
                    if len(data) < 6:  # Need at least 6 bytes for metadata
                        continue
                    
                    # Parse metadata: [4 bytes size][1 byte codec_type][1 byte is_keyframe]
                    frame_size, codec_type, is_keyframe = struct.unpack(">IBB", data[:6])
                    
                    # Rest of the packet contains the frame data
                    frame_data = data[6:]
                    
                    # If we didn't get the full frame, ignore this packet
                    if len(frame_data) != frame_size:
                        continue
                    
                    try:
                        # Decode frame
                        frame = self._decode_frame(frame_data, codec_type, is_keyframe, None)
                        
                        with self.lock:
                            self.received_internal_frame = frame
                        
                        # Call callback if registered
                        if self.on_internal_frame_received:
                            self.on_internal_frame_received(frame)
                    except RuntimeError as e:
                        print(f"Frame decoding error: {e}")
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
        print(f"Starting external camera H.265 receiver on port {EXTERNAL_STREAM_PORT}")
        sock = self._create_udp_socket(EXTERNAL_STREAM_PORT)
        
        try:
            while self.running:
                try:
                    # Receive packet with metadata and frame data
                    data, addr = sock.recvfrom(self.buffer_size)
                    
                    if len(data) < 6:  # Need at least 6 bytes for metadata
                        continue
                    
                    # Parse metadata: [4 bytes size][1 byte codec_type][1 byte is_keyframe]
                    frame_size, codec_type, is_keyframe = struct.unpack(">IBB", data[:6])
                    
                    # Rest of the packet contains the frame data
                    frame_data = data[6:]
                    
                    # If we didn't get the full frame, ignore this packet
                    if len(frame_data) != frame_size:
                        continue
                    
                    try:
                        # Decode frame
                        frame = self._decode_frame(frame_data, codec_type, is_keyframe, None)
                        
                        with self.lock:
                            self.received_external_frame = frame
                        
                        # Call callback if registered
                        if self.on_external_frame_received:
                            self.on_external_frame_received(frame)
                    except RuntimeError as e:
                        print(f"Frame decoding error: {e}")
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
        """Send internal camera frames to remote device using H.265."""
        print(f"Starting internal camera sender to {self.remote_ip}:{INTERNAL_STREAM_PORT}")
        
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
                        
                        # Encode frame with H.265 or fallback to JPEG
                        frame_data, codec_type = self._encode_frame(
                            frame, self.internal_encoder, is_keyframe
                        )
                        
                        # Create packet with metadata
                        # Format: [4 bytes size][1 byte codec_type][1 byte is_keyframe][data]
                        frame_size = len(frame_data)
                        metadata = struct.pack(">IBB", frame_size, codec_type, is_keyframe)
                        packet = metadata + frame_data
                        
                        # Send packet
                        sock.sendto(packet, (self.remote_ip, INTERNAL_STREAM_PORT))
                    
                    # Control frame rate
                    time.sleep(1.0 / FRAME_RATE)
                except Exception as e:
                    if self.running and self.internal_sending:
                        print(f"Error in internal sender: {e}")
                        time.sleep(1.0)
        finally:
            sock.close()
            print("Internal camera sender stopped")
    
    def _external_sender_loop(self) -> None:
        """Send external camera frames to remote device using H.265."""
        print(f"Starting external camera sender to {self.remote_ip}:{EXTERNAL_STREAM_PORT}")
        
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
                        
                        # Encode frame with H.265 or fallback to JPEG
                        frame_data, codec_type = self._encode_frame(
                            frame, self.external_encoder, is_keyframe
                        )
                        
                        # Create packet with metadata
                        # Format: [4 bytes size][1 byte codec_type][1 byte is_keyframe][data]
                        frame_size = len(frame_data)
                        metadata = struct.pack(">IBB", frame_size, codec_type, is_keyframe)
                        packet = metadata + frame_data
                        
                        # Send packet
                        sock.sendto(packet, (self.remote_ip, EXTERNAL_STREAM_PORT))
                    
                    # Control frame rate
                    time.sleep(1.0 / FRAME_RATE)
                except Exception as e:
                    if self.running and self.external_sending:
                        print(f"Error in external sender: {e}")
                        time.sleep(1.0)
        finally:
            sock.close()
            print("External camera sender stopped")


# Add a better method for direct H.265 encoding and decoding using GStreamer
# Helper class removed since we're directly using GStreamer in the main class methods


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
