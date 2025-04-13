"""
Video streaming module for the Dans le Blanc des Yeux installation.
Handles sending and receiving video streams between devices using H.265 encoding.

Streaming Logic:
1. When remote device has pressure=true and local doesn't: Send external PiCamera feed
2. When local device has pressure=true and remote doesn't: Receive remote's external PiCamera feed
3. When both have pressure=true: Send internal camera feed, receive remote's internal camera feed
4. When neither has pressure: No streaming required
"""

"""
Modified VideoStreamer with optimized GStreamer pipeline management.
Only relevant sections are shown - these should replace the corresponding
sections in your original code.
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

# Global import of GStreamer
try:
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst, GLib
    # Initialize GStreamer once
    if not Gst.is_initialized():
        Gst.init(None)
    GSTREAMER_AVAILABLE = True
except ImportError:
    GSTREAMER_AVAILABLE = False
    print("Warning: GStreamer Python bindings not available")


class VideoStreamer:
    """Handles video streaming between devices using H.265 encoding with optimized pipelines."""
    
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
        
        # Pipeline management
        self.pipelines = {
            "internal_encoder": None,
            "external_encoder": None,
            "internal_decoder": None,
            "external_decoder": None
        }
        
        # Pipeline elements
        self.pipeline_elements = {
            "internal_encoder": {},
            "external_encoder": {},
            "internal_decoder": {},
            "external_decoder": {}
        }
        
        # Pipeline locks to manage concurrent access
        self.pipeline_locks = {
            "internal_encoder": threading.Lock(),
            "external_encoder": threading.Lock(),
            "internal_decoder": threading.Lock(),
            "external_decoder": threading.Lock()
        }
        
        # Callbacks for new frames
        self.on_internal_frame_received = None
        self.on_external_frame_received = None
        
        # Register as observer for state changes
        system_state.add_observer(self._on_state_change)
        
        # Check if GStreamer is available
        self.has_gstreamer = GSTREAMER_AVAILABLE
        
        print(f"Video streamer initialized with remote IP: {remote_ip}")
        print(f"GStreamer available: {self.has_gstreamer}")
    
    def start(self) -> bool:
        """Start the video streaming system."""
        print("Starting video streamer with H.265 encoding...")
        self.running = True
        
        if not self.has_gstreamer:
            print("WARNING: GStreamer not available. Streaming will be limited.")
        
        # Create decoder pipelines in advance - they're used regardless of state
        self._create_decoder_pipelines()
        
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

        # Release GStreamer pipelines
        self._release_pipelines()
        
        print("Video streamer stopped")
    
    def _create_decoder_pipelines(self):
        """Create GStreamer pipelines for decoding."""
        if not self.has_gstreamer:
            return
            
        try:
            # Create internal frame decoder pipeline
            with self.pipeline_locks["internal_decoder"]:
                pipeline_str = (
                    "appsrc name=src emit-signals=true is-live=true format=time ! "
                    "h265parse ! avdec_h265 ! videoconvert ! "
                    "video/x-raw,format=BGR ! appsink name=sink emit-signals=true sync=false"
                )
                
                self.pipelines["internal_decoder"] = Gst.parse_launch(pipeline_str)
                self.pipeline_elements["internal_decoder"]["src"] = self.pipelines["internal_decoder"].get_by_name("src")
                self.pipeline_elements["internal_decoder"]["sink"] = self.pipelines["internal_decoder"].get_by_name("sink")
                
                # Configure appsrc
                self.pipeline_elements["internal_decoder"]["src"].set_property("stream-type", 0)  # 0 = GST_APP_STREAM_TYPE_STREAM
                self.pipeline_elements["internal_decoder"]["src"].set_property("format", Gst.Format.TIME)
                self.pipeline_elements["internal_decoder"]["src"].set_property("do-timestamp", True)
                
                # Start pipeline in PAUSED state
                self.pipelines["internal_decoder"].set_state(Gst.State.PAUSED)
                
            # Create external frame decoder pipeline
            with self.pipeline_locks["external_decoder"]:
                pipeline_str = (
                    "appsrc name=src emit-signals=true is-live=true format=time ! "
                    "h265parse ! avdec_h265 ! videoconvert ! "
                    "video/x-raw,format=BGR ! appsink name=sink emit-signals=true sync=false"
                )
                
                self.pipelines["external_decoder"] = Gst.parse_launch(pipeline_str)
                self.pipeline_elements["external_decoder"]["src"] = self.pipelines["external_decoder"].get_by_name("src")
                self.pipeline_elements["external_decoder"]["sink"] = self.pipelines["external_decoder"].get_by_name("sink")
                
                # Configure appsrc
                self.pipeline_elements["external_decoder"]["src"].set_property("stream-type", 0)  # 0 = GST_APP_STREAM_TYPE_STREAM
                self.pipeline_elements["external_decoder"]["src"].set_property("format", Gst.Format.TIME)
                self.pipeline_elements["external_decoder"]["src"].set_property("do-timestamp", True)
                
                # Start pipeline in PAUSED state
                self.pipelines["external_decoder"].set_state(Gst.State.PAUSED)
                
            print("Decoder pipelines created successfully")
                
        except Exception as e:
            print(f"Error creating decoder pipelines: {e}")
            self._release_pipeline("internal_decoder")
            self._release_pipeline("external_decoder")
    
    def _create_encoder_pipeline(self, pipeline_name, width, height):
        """
        Create a GStreamer pipeline for encoding.
        
        Args:
            pipeline_name: Name of the pipeline ("internal_encoder" or "external_encoder")
            width: Frame width
            height: Frame height
        """
        if not self.has_gstreamer:
            return False
            
        try:
            with self.pipeline_locks[pipeline_name]:
                # Release existing pipeline if any
                self._release_pipeline(pipeline_name)
                
                # Create pipeline string
                pipeline_str = (
                    f"appsrc name=src emit-signals=true is-live=true format=time caps=video/x-raw,format=BGR,width={width},height={height},framerate={FRAME_RATE}/1 ! "
                    f"videoconvert ! video/x-raw,format=I420 ! "
                    f"x265enc bitrate={H265_BITRATE//1000} key-int-max={KEYFRAME_INTERVAL} ! "
                    f"video/x-h265,profile=main ! h265parse ! "
                    f"appsink name=sink emit-signals=true sync=false"
                )
                
                # Create pipeline
                self.pipelines[pipeline_name] = Gst.parse_launch(pipeline_str)
                
                # Get elements
                self.pipeline_elements[pipeline_name]["src"] = self.pipelines[pipeline_name].get_by_name("src")
                self.pipeline_elements[pipeline_name]["sink"] = self.pipelines[pipeline_name].get_by_name("sink")
                
                # Configure appsrc
                self.pipeline_elements[pipeline_name]["src"].set_property("stream-type", 0)  # 0 = GST_APP_STREAM_TYPE_STREAM
                self.pipeline_elements[pipeline_name]["src"].set_property("format", Gst.Format.TIME)
                self.pipeline_elements[pipeline_name]["src"].set_property("do-timestamp", True)
                
                # Start pipeline in PAUSED state (ready to encode)
                self.pipelines[pipeline_name].set_state(Gst.State.PLAYING)
                
                print(f"Created {pipeline_name} pipeline: {width}x{height}")
                return True
                
        except Exception as e:
            print(f"Error creating {pipeline_name} pipeline: {e}")
            self._release_pipeline(pipeline_name)
            return False
    
    def _release_pipeline(self, pipeline_name):
        """
        Release a specific GStreamer pipeline.
        
        Args:
            pipeline_name: Name of the pipeline to release
        """
        if not self.has_gstreamer:
            return
            
        with self.pipeline_locks[pipeline_name]:
            if self.pipelines[pipeline_name] is not None:
                try:
                    # Send EOS to clean up pending buffers
                    if pipeline_name.endswith("encoder") and "src" in self.pipeline_elements[pipeline_name]:
                        self.pipeline_elements[pipeline_name]["src"].send_event(Gst.Event.new_eos())
                    
                    # Wait for EOS to propagate (with timeout)
                    if self.pipelines[pipeline_name] is not None:
                        self.pipelines[pipeline_name].set_state(Gst.State.NULL)
                    
                    # Clear elements dictionary
                    self.pipeline_elements[pipeline_name].clear()
                    
                    # Clear pipeline reference
                    self.pipelines[pipeline_name] = None
                    
                except Exception as e:
                    print(f"Error releasing {pipeline_name} pipeline: {e}")
    
    def _release_pipelines(self):
        """Release all GStreamer pipelines."""
        if not self.has_gstreamer:
            return
            
        for pipeline_name in self.pipelines.keys():
            self._release_pipeline(pipeline_name)
    
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
            
            # Create encoder pipeline
            if not self._create_encoder_pipeline("internal_encoder", width, height):
                print("Failed to create internal encoder pipeline")
                return False
            
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
            self._release_pipeline("internal_encoder")
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
                
            height, width = test_frame.shape[:2]
            
            # Create encoder pipeline
            if not self._create_encoder_pipeline("external_encoder", width, height):
                print("Failed to create external encoder pipeline")
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
            self._release_pipeline("external_encoder")
            return False
    
    def _stop_internal_stream(self) -> None:
        """Stop streaming the internal camera."""
        if self.internal_sending:
            self.internal_sending = False
            self._release_pipeline("internal_encoder")
            print("Stopped internal camera stream")
    
    def _stop_external_stream(self) -> None:
        """Stop streaming the external camera."""
        if self.external_sending:
            self.external_sending = False
            self._release_pipeline("external_encoder")
            print("Stopped external camera stream")
    
    def _encode_frame(self, frame: np.ndarray, camera_type: str, is_keyframe: bool = False) -> Tuple[bytes, int]:
        """
        Encode a frame using H.265 with persistent pipeline.
        
        Args:
            frame: Input frame to encode
            camera_type: "internal" or "external"
            is_keyframe: Force keyframe generation (only used for JPEG fallback)
            
        Returns:
            Tuple of (encoded_data, codec_type)
        """
        if not self.has_gstreamer:
            # Fallback to JPEG encoding if GStreamer not available
            ret, jpeg_data = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
            if not ret:
                raise RuntimeError("JPEG encoding failed")
            return jpeg_data.tobytes(), CODEC_JPEG
        
        pipeline_name = f"{camera_type}_encoder"
        
        try:
            with self.pipeline_locks[pipeline_name]:
                # Make sure pipeline exists
                if self.pipelines[pipeline_name] is None:
                    # Create pipeline if it doesn't exist
                    height, width = frame.shape[:2]
                    if not self._create_encoder_pipeline(pipeline_name, width, height):
                        raise RuntimeError("Failed to create encoder pipeline")
                
                # Push frame to pipeline
                src = self.pipeline_elements[pipeline_name]["src"]
                sink = self.pipeline_elements[pipeline_name]["sink"]
                
                # Ensure frame is BGR and contiguous
                if frame.dtype != np.uint8:
                    frame = frame.astype(np.uint8)
                if not frame.flags['C_CONTIGUOUS']:
                    frame = np.ascontiguousarray(frame)
                
                # Create GStreamer buffer from numpy array
                height, width = frame.shape[:2]
                frame_size = width * height * 3  # BGR format
                
                # Create buffer
                buffer = Gst.Buffer.new_allocate(None, frame_size, None)
                buffer.fill(0, frame.tobytes())
                
                # Set buffer timestamp
                buffer.pts = Gst.CLOCK_TIME_NONE
                buffer.dts = Gst.CLOCK_TIME_NONE
                buffer.duration = Gst.CLOCK_TIME_NONE
                
                # Push buffer to source
                ret = src.emit("push-buffer", buffer)
                if not ret:
                    raise RuntimeError("Failed to push buffer to encoder")
                
                # Pull encoded data from sink
                sample = sink.try_pull_sample(Gst.SECOND)
                if not sample:
                    raise RuntimeError("No sample received from encoder")
                
                # Extract data from sample
                buffer = sample.get_buffer()
                success, mapinfo = buffer.map(Gst.MapFlags.READ)
                
                if not success:
                    raise RuntimeError("Failed to map buffer")
                
                # Copy data to avoid issues after unmap
                encoded_data = bytes(mapinfo.data)
                buffer.unmap(mapinfo)
                
                if len(encoded_data) == 0:
                    raise RuntimeError("Empty encoded data")
                
                return encoded_data, CODEC_H265
                
        except Exception as e:
            print(f"Error in H.265 encoding ({camera_type}): {e}")
            # Try to recreate the pipeline on error
            self._release_pipeline(pipeline_name)
            
            # Fallback to JPEG encoding
            ret, jpeg_data = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
            if not ret:
                raise RuntimeError("JPEG encoding failed")
            return jpeg_data.tobytes(), CODEC_JPEG
    
    def _decode_frame(self, data: bytes, codec_type: int, is_keyframe: bool, camera_type: str) -> np.ndarray:
        """
        Decode a frame from received data using persistent pipeline.
        
        Args:
            data: Encoded frame data
            codec_type: Type of encoding (CODEC_JPEG or CODEC_H265)
            is_keyframe: Whether this is a keyframe
            camera_type: "internal" or "external"
            
        Returns:
            Decoded frame
        """
        # For JPEG, use OpenCV decoder (faster and more reliable)
        if codec_type == CODEC_JPEG:
            frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                raise RuntimeError("Failed to decode JPEG data")
            return frame
            
        # For H.265, use GStreamer pipeline
        if not self.has_gstreamer:
            raise RuntimeError("Cannot decode H.265: GStreamer not available")
        
        pipeline_name = f"{camera_type}_decoder"
        
        try:
            with self.pipeline_locks[pipeline_name]:
                # Make sure pipeline exists
                if self.pipelines[pipeline_name] is None:
                    raise RuntimeError("Decoder pipeline not initialized")
                
                # Get pipeline elements
                src = self.pipeline_elements[pipeline_name]["src"]
                sink = self.pipeline_elements[pipeline_name]["sink"]
                
                # Ensure pipeline is in PLAYING state
                self.pipelines[pipeline_name].set_state(Gst.State.PLAYING)
                
                # Create buffer with H.265 data
                buffer = Gst.Buffer.new_allocate(None, len(data), None)
                buffer.fill(0, data)
                
                # Set buffer metadata
                buffer.pts = Gst.CLOCK_TIME_NONE
                buffer.dts = Gst.CLOCK_TIME_NONE
                buffer.duration = Gst.CLOCK_TIME_NONE
                
                # Push buffer to source
                ret = src.emit("push-buffer", buffer)
                if not ret:
                    raise RuntimeError("Failed to push buffer to decoder")
                
                # Pull sample from sink
                sample = sink.try_pull_sample(Gst.SECOND / 10)  # Short timeout
                if not sample:
                    raise RuntimeError("No sample received from decoder")
                
                # Extract buffer from sample
                buffer = sample.get_buffer()
                success, mapinfo = buffer.map(Gst.MapFlags.READ)
                
                if not success:
                    raise RuntimeError("Failed to map buffer")
                
                # Get frame dimensions from caps
                caps = sample.get_caps()
                structure = caps.get_structure(0)
                width = structure.get_value("width")
                height = structure.get_value("height")
                
                # Convert buffer to numpy array
                frame_data = mapinfo.data
                frame = np.frombuffer(frame_data, dtype=np.uint8)
                frame = frame.reshape((height, width, 3))
                
                # Make a copy before unmap
                frame = frame.copy()
                buffer.unmap(mapinfo)
                
                return frame
                
        except Exception as e:
            print(f"Error in H.265 decoding ({camera_type}): {e}")
            # Try to recreate the pipeline on error
            self._create_decoder_pipelines()
            raise RuntimeError(f"H.265 decoding failed: {e}")
    
    def _internal_sender_loop(self) -> None:
        """Send internal camera frames to remote device using H.265."""
        print(f"Starting internal camera sender to {self.remote_ip}:{INTERNAL_STREAM_PORT}")
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        try:
            frame_timer = time.time()
            frame_count = 0
            
            while self.running and self.internal_sending:
                loop_start = time.time()
                
                try:
                    # Get latest frame from internal camera
                    frame = self.camera_manager.get_internal_frame()
                    
                    if frame is not None:
                        # Determine if this should be a keyframe
                        is_keyframe = (self.frame_count["internal"] % KEYFRAME_INTERVAL) == 0
                        self.frame_count["internal"] += 1
                        
                        # Encode frame with persistent pipeline
                        frame_data, codec_type = self._encode_frame(
                            frame, "internal", is_keyframe
                        )
                        
                        # Create packet with metadata
                        # Format: [4 bytes size][1 byte codec_type][1 byte is_keyframe][data]
                        frame_size = len(frame_data)
                        metadata = struct.pack(">IBB", frame_size, codec_type, is_keyframe)
                        packet = metadata + frame_data
                        
                        # Send packet
                        sock.sendto(packet, (self.remote_ip, INTERNAL_STREAM_PORT))
                        
                        # Log performance periodically
                        frame_count += 1
                        if frame_count % 100 == 0:
                            elapsed = time.time() - frame_timer
                            fps = 100 / elapsed if elapsed > 0 else 0
                            data_rate = sum(len(packet) for _ in range(100)) * 8 / (elapsed * 1000)  # kbps
                            print(f"Internal stream: {fps:.1f} fps, {data_rate:.1f} kbps")
                            frame_timer = time.time()
                    
                    # Control frame rate with adaptive sleep
                    elapsed = time.time() - loop_start
                    sleep_time = max(0.001, (1.0 / FRAME_RATE) - elapsed)
                    time.sleep(sleep_time)
                    
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
            frame_timer = time.time()
            frame_count = 0
            
            while self.running and self.external_sending:
                loop_start = time.time()
                
                try:
                    # Get latest frame from external camera
                    frame = self.camera_manager.get_external_frame()
                    
                    if frame is not None:
                        # Determine if this should be a keyframe
                        is_keyframe = (self.frame_count["external"] % KEYFRAME_INTERVAL) == 0
                        self.frame_count["external"] += 1
                        
                        # Encode frame with persistent pipeline
                        frame_data, codec_type = self._encode_frame(
                            frame, "external", is_keyframe
                        )
                        
                        # Create packet with metadata
                        # Format: [4 bytes size][1 byte codec_type][1 byte is_keyframe][data]
                        frame_size = len(frame_data)
                        metadata = struct.pack(">IBB", frame_size, codec_type, is_keyframe)
                        packet = metadata + frame_data
                        
                        # Send packet
                        sock.sendto(packet, (self.remote_ip, EXTERNAL_STREAM_PORT))
                        
                        # Log performance periodically
                        frame_count += 1
                        if frame_count % 100 == 0:
                            elapsed = time.time() - frame_timer
                            fps = 100 / elapsed if elapsed > 0 else 0
                            data_rate = sum(len(packet) for _ in range(100)) * 8 / (elapsed * 1000)  # kbps
                            print(f"External stream: {fps:.1f} fps, {data_rate:.1f} kbps")
                            frame_timer = time.time()
                    
                    # Control frame rate with adaptive sleep
                    elapsed = time.time() - loop_start
                    sleep_time = max(0.001, (1.0 / FRAME_RATE) - elapsed)
                    time.sleep(sleep_time)
                    
                except Exception as e:
                    if self.running and self.external_sending:
                        print(f"Error in external sender: {e}")
                        time.sleep(1.0)
        finally:
            sock.close()
            print("External camera sender stopped")
            
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
                        # Decode frame using persistent pipeline
                        frame = self._decode_frame(frame_data, codec_type, is_keyframe, "internal")
                        
                        with self.lock:
                            self.received_internal_frame = frame
                        
                        # Call callback if registered
                        if self.on_internal_frame_received:
                            self.on_internal_frame_received(frame)
                    except RuntimeError as e:
                        print(f"Internal frame decoding error: {e}")
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
                        # Decode frame using persistent pipeline
                        frame = self._decode_frame(frame_data, codec_type, is_keyframe, "external")
                        
                        with self.lock:
                            self.received_external_frame = frame
                        
                        # Call callback if registered
                        if self.on_external_frame_received:
                            self.on_external_frame_received(frame)
                    except RuntimeError as e:
                        print(f"External frame decoding error: {e}")
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
