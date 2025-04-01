"""
GStreamer-based video streaming module for the Dans le Blanc des Yeux installation.
Replaces the custom UDP streaming with GStreamer pipelines for better compression and CPU usage.

Streaming Logic:
1. When remote device has pressure=true and local doesn't: Send external PiCamera feed
2. When local device has pressure=true and remote doesn't: Receive remote's external PiCamera feed
3. When both have pressure=true: Send internal camera feed, receive remote's internal camera feed
4. When neither has pressure: No streaming required
"""

import os
import time
import threading
import numpy as np
import cv2
import gi
from typing import Dict, Optional, Tuple, List, Callable

# Import GStreamer
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib, GObject

from system_state import system_state
from camera_manager import CameraManager

# Port configuration - using different ports for GStreamer RTP streams
INTERNAL_STREAM_PORT = 5000  # Port for internal camera stream
EXTERNAL_STREAM_PORT = 5001  # Port for external camera stream

class GStreamerVideoStreamer:
    """Handles video streaming between devices using GStreamer."""
    
    def __init__(self, camera_manager: CameraManager, remote_ip: str):
        self.camera_manager = camera_manager
        self.remote_ip = remote_ip
        
        # Initialize GStreamer
        Gst.init(None)
        
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
        self.bitrate = 2000000  # 2 Mbps default bitrate
        
        # Pipeline objects
        self.internal_send_pipeline = None
        self.external_send_pipeline = None
        self.internal_receive_pipeline = None
        self.external_receive_pipeline = None
        
        # GLib main loop for GStreamer
        self.main_loop = None
        self.main_loop_thread = None
        
        # Callbacks
        self.on_internal_frame_received = None
        self.on_external_frame_received = None
        
        # Register as observer for state changes
        system_state.add_observer(self._on_state_change)
        
        print(f"GStreamer video streamer initialized with remote IP: {remote_ip}")
    
    def start(self) -> bool:
        """Start the video streaming system."""
        print("Starting GStreamer video streamer...")
        self.running = True
        
        # Start GLib main loop in a separate thread
        self.main_loop = GLib.MainLoop()
        self.main_loop_thread = threading.Thread(target=self._run_main_loop)
        self.main_loop_thread.daemon = True
        self.main_loop_thread.start()
        
        # Start receiver pipelines
        self._start_receiver_pipelines()
        
        # Check initial state to see if we need to start streaming right away
        self._update_streaming_based_on_state()
        
        print("GStreamer video streamer started")
        return True
    
    def stop(self) -> None:
        """Stop all streaming and release resources."""
        print("Stopping GStreamer video streamer...")
        self.running = False
        
        # Stop any active streaming
        self._stop_all_streams()
        
        # Stop GLib main loop
        if self.main_loop is not None and self.main_loop.is_running():
            self.main_loop.quit()
        
        # Wait for threads to finish
        if self.main_loop_thread is not None:
            self.main_loop_thread.join(timeout=2.0)
        
        for thread in self.threads:
            thread.join(timeout=1.0)
        
        print("GStreamer video streamer stopped")
    
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
    
    def _run_main_loop(self) -> None:
        """Run GLib main loop for GStreamer."""
        try:
            self.main_loop.run()
        except Exception as e:
            print(f"Error in GStreamer main loop: {e}")
        finally:
            print("GStreamer main loop stopped")
    
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
    
    def _start_receiver_pipelines(self) -> None:
        """Start GStreamer pipelines to receive video streams."""
        # Start internal camera receiver pipeline
        try:
            self._start_internal_receiver_pipeline()
        except Exception as e:
            print(f"Error starting internal receiver pipeline: {e}")
        
        # Start external camera receiver pipeline
        try:
            self._start_external_receiver_pipeline()
        except Exception as e:
            print(f"Error starting external receiver pipeline: {e}")
    
    def _start_internal_stream(self) -> bool:
        """Start streaming the internal camera to the remote device."""
        if self.internal_sending:
            return True
        
        if not self.camera_manager.is_internal_camera_available():
            print("Internal camera not available")
            return False
        
        try:
            # Start sender pipeline
            self._start_internal_sender_pipeline()
            
            self.internal_sending = True
            print(f"Started internal camera GStreamer stream to {self.remote_ip}:{INTERNAL_STREAM_PORT}")
            return True
        except Exception as e:
            print(f"Failed to start internal camera GStreamer stream: {e}")
            return False
    
    def _start_external_stream(self) -> bool:
        """Start streaming the external camera to the remote device."""
        if self.external_sending:
            return True
        
        if not self.camera_manager.is_external_camera_available():
            print("External camera not available")
            return False
        
        try:
            # Start sender pipeline
            self._start_external_sender_pipeline()
            
            self.external_sending = True
            print(f"Started external camera GStreamer stream to {self.remote_ip}:{EXTERNAL_STREAM_PORT}")
            return True
        except Exception as e:
            print(f"Failed to start external camera GStreamer stream: {e}")
            return False
    
    def _stop_internal_stream(self) -> None:
        """Stop streaming the internal camera."""
        if self.internal_sending:
            if self.internal_send_pipeline is not None:
                self.internal_send_pipeline.set_state(Gst.State.NULL)
                self.internal_send_pipeline = None
            self.internal_sending = False
            print("Stopped internal camera GStreamer stream")
    
    def _stop_external_stream(self) -> None:
        """Stop streaming the external camera."""
        if self.external_sending:
            if self.external_send_pipeline is not None:
                self.external_send_pipeline.set_state(Gst.State.NULL)
                self.external_send_pipeline = None
            self.external_sending = False
            print("Stopped external camera GStreamer stream")
    
    def _stop_all_streams(self) -> None:
        """Stop all active streams."""
        self._stop_internal_stream()
        self._stop_external_stream()
        print("All GStreamer streams stopped")
    
    def _start_internal_sender_pipeline(self) -> None:
        """Create and start GStreamer pipeline for sending internal camera frames."""
        if self.internal_send_pipeline is not None:
            self.internal_send_pipeline.set_state(Gst.State.NULL)
        
        # Create a new sender pipeline for H.264 encoding and RTP streaming
        # We'll use appsrc to feed frames from our camera_manager to the pipeline
        pipeline_str = (
            f"appsrc name=src is-live=true format=GST_FORMAT_TIME ! "
            f"videoconvert ! video/x-raw,format=I420 ! "
            f"x264enc tune=zerolatency bitrate={self.bitrate//1000} speed-preset=ultrafast ! "  # H.264 encoder
            f"rtph264pay config-interval=1 ! "  # RTP payloader for H.264
            f"udpsink host={self.remote_ip} port={INTERNAL_STREAM_PORT} sync=false"
        )
        
        # Try to use hardware acceleration if available (for Raspberry Pi)
        try:
            # Check if we have hardware encoder available
            # Try various hardware encoders that might be available on the Raspberry Pi
            # First check for omxh264enc (older Pis)
            try_encoders = [
                # omxh264enc - older Raspberry Pi models
                f"appsrc name=src is-live=true format=GST_FORMAT_TIME ! "
                f"videoconvert ! video/x-raw,format=I420 ! "
                f"omxh264enc control-rate=variable target-bitrate={self.bitrate} ! "
                f"video/x-h264,profile=main ! "
                f"rtph264pay config-interval=1 ! "
                f"udpsink host={self.remote_ip} port={INTERNAL_STREAM_PORT} sync=false",
                
                # v4l2h264enc - V4L2 hardware encoder
                f"appsrc name=src is-live=true format=GST_FORMAT_TIME ! "
                f"videoconvert ! video/x-raw,format=I420 ! "
                f"v4l2h264enc extra-controls=controls,h264_profile=4,video_bitrate={self.bitrate} ! "
                f"video/x-h264,profile=main ! "
                f"rtph264pay config-interval=1 ! "
                f"udpsink host={self.remote_ip} port={INTERNAL_STREAM_PORT} sync=false",
                
                # nvh264enc - NVIDIA hardware encoder (if using Jetson)
                f"appsrc name=src is-live=true format=GST_FORMAT_TIME ! "
                f"videoconvert ! video/x-raw,format=I420 ! "
                f"nvh264enc bitrate={self.bitrate//1000} ! "
                f"video/x-h264,profile=main ! "
                f"rtph264pay config-interval=1 ! "
                f"udpsink host={self.remote_ip} port={INTERNAL_STREAM_PORT} sync=false",
                
                # vaapih264enc - For systems with VA-API support
                f"appsrc name=src is-live=true format=GST_FORMAT_TIME ! "
                f"videoconvert ! video/x-raw,format=I420 ! "
                f"vaapih264enc rate-control=cbr bitrate={self.bitrate//1000} ! "
                f"video/x-h264,profile=main ! "
                f"rtph264pay config-interval=1 ! "
                f"udpsink host={self.remote_ip} port={INTERNAL_STREAM_PORT} sync=false",
            ]
            
            # Try each encoder until one works
            hardware_pipeline_str = None
            for encoder_pipeline in try_encoders:
                try:
                    # Try to create the pipeline with this encoder
                    test_pipeline = Gst.parse_launch(encoder_pipeline)
                    # If we get here, the encoder exists
                    hardware_pipeline_str = encoder_pipeline
                    test_pipeline.set_state(Gst.State.NULL)
                    print(f"Found working hardware encoder: {encoder_pipeline.split('!')[2].strip()}")
                    break
                except Exception as e:
                    # This encoder doesn't exist, try the next one
                    continue
            
            # Create pipeline
            self.internal_send_pipeline = Gst.parse_launch(hardware_pipeline_str)
            print("Using hardware-accelerated H.264 encoder for internal stream")
        except Exception as hw_error:
            print(f"Hardware acceleration not available: {hw_error}")
            print("Falling back to software x264 encoder")
            # Fall back to software encoding
            self.internal_send_pipeline = Gst.parse_launch(pipeline_str)
        
        # Get appsrc element
        src = self.internal_send_pipeline.get_by_name("src")
        
        # Configure appsrc
        src.set_property("format", Gst.Format.TIME)
        src.set_property("is-live", True)
        src.set_property("do-timestamp", True)
        caps = Gst.Caps.from_string(f"video/x-raw,format=BGR,width={self.frame_width},height={self.frame_height},framerate=30/1")
        src.set_property("caps", caps)
        
        # Start the pipeline
        self.internal_send_pipeline.set_state(Gst.State.PLAYING)
        
        # Start thread to feed frames to the pipeline
        sender_thread = threading.Thread(target=self._internal_frame_sender, args=(src,))
        sender_thread.daemon = True
        sender_thread.start()
        self.threads.append(sender_thread)
    
    def _start_external_sender_pipeline(self) -> None:
        """Create and start GStreamer pipeline for sending external camera frames."""
        if self.external_send_pipeline is not None:
            self.external_send_pipeline.set_state(Gst.State.NULL)
        
        # Create a new sender pipeline for H.264 encoding and RTP streaming
        pipeline_str = (
            f"appsrc name=src is-live=true format=GST_FORMAT_TIME ! "
            f"videoconvert ! video/x-raw,format=I420 ! "
            f"x264enc tune=zerolatency bitrate={self.bitrate//1000} speed-preset=ultrafast ! "
            f"rtph264pay config-interval=1 ! "
            f"udpsink host={self.remote_ip} port={EXTERNAL_STREAM_PORT} sync=false"
        )
        
        # Try to use hardware acceleration if available
        try:
            # Based on available encoders, prioritize the ones we have
            try_encoders = [
                # avenc_h264_omx - OpenMAX IL encoder for Raspberry Pi
                f"appsrc name=src is-live=true format=GST_FORMAT_TIME ! "
                f"videoconvert ! video/x-raw,format=I420 ! "
                f"avenc_h264_omx bitrate={self.bitrate} ! "
                f"video/x-h264,profile=main ! "
                f"rtph264pay config-interval=1 ! "
                f"udpsink host={self.remote_ip} port={EXTERNAL_STREAM_PORT} sync=false",
                
                # Software encoders as fallback
                f"appsrc name=src is-live=true format=GST_FORMAT_TIME ! "
                f"videoconvert ! video/x-raw,format=I420 ! "
                f"x264enc tune=zerolatency bitrate={self.bitrate//1000} speed-preset=superfast ! "
                f"video/x-h264,profile=main ! "
                f"rtph264pay config-interval=1 ! "
                f"udpsink host={self.remote_ip} port={EXTERNAL_STREAM_PORT} sync=false",
                
                # Even simpler pipeline as last resort
                f"appsrc name=src is-live=true format=GST_FORMAT_TIME ! "
                f"videoconvert ! x264enc tune=zerolatency ! rtph264pay ! "
                f"udpsink host={self.remote_ip} port={EXTERNAL_STREAM_PORT} sync=false"
            ]
            
            # Try each encoder until one works
            hardware_pipeline_str = None
            for encoder_pipeline in try_encoders:
                try:
                    # Try to create the pipeline with this encoder
                    test_pipeline = Gst.parse_launch(encoder_pipeline)
                    # If we get here, the encoder exists
                    hardware_pipeline_str = encoder_pipeline
                    test_pipeline.set_state(Gst.State.NULL)
                    print(f"Found working hardware encoder: {encoder_pipeline.split('!')[2].strip()}")
                    break
                except Exception as e:
                    # This encoder doesn't exist, try the next one
                    continue
            
            # Create pipeline
            self.external_send_pipeline = Gst.parse_launch(hardware_pipeline_str)
            print("Using hardware-accelerated H.264 encoder for external stream")
        except Exception:
            print("Hardware acceleration not available, using software encoder")
            # Fall back to software encoding
            self.external_send_pipeline = Gst.parse_launch(pipeline_str)
        
        # Get appsrc element
        src = self.external_send_pipeline.get_by_name("src")
        
        # Configure appsrc
        src.set_property("format", Gst.Format.TIME)
        src.set_property("is-live", True)
        src.set_property("do-timestamp", True)
        caps = Gst.Caps.from_string(f"video/x-raw,format=BGR,width={self.frame_width},height={self.frame_height},framerate=30/1")
        src.set_property("caps", caps)
        
        # Start the pipeline
        self.external_send_pipeline.set_state(Gst.State.PLAYING)
        
        # Start thread to feed frames to the pipeline
        sender_thread = threading.Thread(target=self._external_frame_sender, args=(src,))
        sender_thread.daemon = True
        sender_thread.start()
        self.threads.append(sender_thread)
    
    def _start_internal_receiver_pipeline(self) -> None:
        """Create and start GStreamer pipeline for receiving internal camera frames."""
        if self.internal_receive_pipeline is not None:
            self.internal_receive_pipeline.set_state(Gst.State.NULL)
        
        # Try to create a pipeline that uses hardware decoding if available
        try_pipelines = [
            # Try hardware-accelerated decoder first (omx for Raspberry Pi)
            f"udpsrc port={INTERNAL_STREAM_PORT} caps=\"application/x-rtp,media=video,encoding-name=H264,payload=96\" ! "
            f"rtpjitterbuffer latency=50 ! rtph264depay ! h264parse ! "
            f"omxh264dec ! videoconvert ! video/x-raw,format=BGR ! "
            f"appsink name=sink emit-signals=true sync=false",
            
            # Try VA-API decoder
            f"udpsrc port={INTERNAL_STREAM_PORT} caps=\"application/x-rtp,media=video,encoding-name=H264,payload=96\" ! "
            f"rtpjitterbuffer latency=50 ! rtph264depay ! h264parse ! "
            f"vaapih264dec ! videoconvert ! video/x-raw,format=BGR ! "
            f"appsink name=sink emit-signals=true sync=false",
            
            # Fallback to software decoder
            f"udpsrc port={INTERNAL_STREAM_PORT} caps=\"application/x-rtp,media=video,encoding-name=H264,payload=96\" ! "
            f"rtpjitterbuffer latency=50 ! rtph264depay ! h264parse ! "
            f"avdec_h264 ! videoconvert ! video/x-raw,format=BGR ! "
            f"appsink name=sink emit-signals=true sync=false"
        ]
        
        # Try each pipeline until one works
        pipeline_str = None
        for pipeline in try_pipelines:
            try:
                # Try to create this pipeline
                test_pipeline = Gst.parse_launch(pipeline)
                # If we get here, the pipeline can be created
                pipeline_str = pipeline
                test_pipeline.set_state(Gst.State.NULL)
                # Extract the decoder element name for logging
                decoder = pipeline.split('!')[3].strip()
                print(f"Using H.264 decoder: {decoder}")
                break
            except Exception as e:
                # This pipeline doesn't work, try the next one
                continue
                
        # If none of the pipelines worked, use the default software decoder
        if pipeline_str is None:
            print("All decoder pipelines failed, using minimal pipeline")
            pipeline_str = (
                f"udpsrc port={INTERNAL_STREAM_PORT} caps=\"application/x-rtp,media=video,encoding-name=H264,payload=96\" ! "
                f"rtpjitterbuffer latency=50 ! rtph264depay ! h264parse ! "
                f"avdec_h264 ! videoconvert ! video/x-raw,format=BGR ! "
                f"appsink name=sink emit-signals=true sync=false"
            )
        
        # Create pipeline
        self.internal_receive_pipeline = Gst.parse_launch(pipeline_str)
        
        # Get appsink element
        sink = self.internal_receive_pipeline.get_by_name("sink")
        
        # Connect to signals for new-sample
        sink.connect("new-sample", self._on_internal_new_sample)
        
        # Start the pipeline
        self.internal_receive_pipeline.set_state(Gst.State.PLAYING)
    
    def _start_external_receiver_pipeline(self) -> None:
        """Create and start GStreamer pipeline for receiving external camera frames."""
        if self.external_receive_pipeline is not None:
            self.external_receive_pipeline.set_state(Gst.State.NULL)
        
        # Try to create a pipeline that uses hardware decoding if available
        try_pipelines = [
            # Try hardware-accelerated decoder first (omx for Raspberry Pi)
            f"udpsrc port={EXTERNAL_STREAM_PORT} caps=\"application/x-rtp,media=video,encoding-name=H264,payload=96\" ! "
            f"rtpjitterbuffer latency=50 ! rtph264depay ! h264parse ! "
            f"omxh264dec ! videoconvert ! video/x-raw,format=BGR ! "
            f"appsink name=sink emit-signals=true sync=false",
            
            # Try VA-API decoder
            f"udpsrc port={EXTERNAL_STREAM_PORT} caps=\"application/x-rtp,media=video,encoding-name=H264,payload=96\" ! "
            f"rtpjitterbuffer latency=50 ! rtph264depay ! h264parse ! "
            f"vaapih264dec ! videoconvert ! video/x-raw,format=BGR ! "
            f"appsink name=sink emit-signals=true sync=false",
            
            # Fallback to software decoder
            f"udpsrc port={EXTERNAL_STREAM_PORT} caps=\"application/x-rtp,media=video,encoding-name=H264,payload=96\" ! "
            f"rtpjitterbuffer latency=50 ! rtph264depay ! h264parse ! "
            f"avdec_h264 ! videoconvert ! video/x-raw,format=BGR ! "
            f"appsink name=sink emit-signals=true sync=false"
        ]
        
        # Try each pipeline until one works
        pipeline_str = None
        for pipeline in try_pipelines:
            try:
                # Try to create this pipeline
                test_pipeline = Gst.parse_launch(pipeline)
                # If we get here, the pipeline can be created
                pipeline_str = pipeline
                test_pipeline.set_state(Gst.State.NULL)
                # Extract the decoder element name for logging
                decoder = pipeline.split('!')[3].strip()
                print(f"Using H.264 decoder: {decoder}")
                break
            except Exception as e:
                # This pipeline doesn't work, try the next one
                continue
                
        # If none of the pipelines worked, use the default software decoder
        if pipeline_str is None:
            print("All decoder pipelines failed, using minimal pipeline")
            pipeline_str = (
                f"udpsrc port={EXTERNAL_STREAM_PORT} caps=\"application/x-rtp,media=video,encoding-name=H264,payload=96\" ! "
                f"rtpjitterbuffer latency=50 ! rtph264depay ! h264parse ! "
                f"avdec_h264 ! videoconvert ! video/x-raw,format=BGR ! "
                f"appsink name=sink emit-signals=true sync=false"
            )
        
        # Create pipeline
        self.external_receive_pipeline = Gst.parse_launch(pipeline_str)
        
        # Get appsink element
        sink = self.external_receive_pipeline.get_by_name("sink")
        
        # Connect to signals for new-sample
        sink.connect("new-sample", self._on_external_new_sample)
        
        # Start the pipeline
        self.external_receive_pipeline.set_state(Gst.State.PLAYING)
    
    def _internal_frame_sender(self, src: Gst.Element) -> None:
        """Thread function to send internal camera frames to GStreamer pipeline."""
        frame_count = 0
        last_report_time = time.time()
        timestamp = 0
        
        try:
            while self.running and self.internal_sending:
                if self.internal_send_pipeline.get_state(0)[1] != Gst.State.PLAYING:
                    time.sleep(0.1)
                    continue
                
                # Get frame from camera manager
                frame = self.camera_manager.get_internal_frame()
                
                if frame is not None:
                    # Make sure frame dimensions match what we specified in caps
                    if frame.shape[1] != self.frame_width or frame.shape[0] != self.frame_height:
                        frame = cv2.resize(frame, (self.frame_width, self.frame_height))
                    
                    # Create GStreamer buffer from numpy array
                    data = frame.tobytes()
                    buf = Gst.Buffer.new_allocate(None, len(data), None)
                    buf.fill(0, data)
                    
                    # Set buffer timestamp and duration
                    buf.pts = timestamp
                    buf.duration = Gst.SECOND // 30  # Assuming 30 fps
                    timestamp += buf.duration
                    
                    # Push buffer to appsrc
                    result = src.emit("push-buffer", buf)
                    if result != Gst.FlowReturn.OK:
                        print(f"Error pushing buffer to internal stream: {result}")
                    
                    # Report stats periodically
                    frame_count += 1
                    if frame_count % 100 == 0:
                        now = time.time()
                        elapsed = now - last_report_time
                        fps = 100 / elapsed if elapsed > 0 else 0
                        print(f"Internal stream: sent {frame_count} frames, {fps:.1f} fps")
                        last_report_time = now
                
                # Control frame rate
                time.sleep(1/30)  # Aim for 30 fps
        except Exception as e:
            print(f"Error in internal frame sender: {e}")
        finally:
            # Send EOS to pipeline
            if self.internal_sending and src is not None:
                src.emit("end-of-stream")
            print("Internal frame sender stopped")
    
    def _external_frame_sender(self, src: Gst.Element) -> None:
        """Thread function to send external camera frames to GStreamer pipeline."""
        frame_count = 0
        last_report_time = time.time()
        timestamp = 0
        
        try:
            while self.running and self.external_sending:
                if self.external_send_pipeline.get_state(0)[1] != Gst.State.PLAYING:
                    time.sleep(0.1)
                    continue
                
                # Get frame from camera manager
                frame = self.camera_manager.get_external_frame()
                
                if frame is not None:
                    # Make sure frame dimensions match what we specified in caps
                    if frame.shape[1] != self.frame_width or frame.shape[0] != self.frame_height:
                        frame = cv2.resize(frame, (self.frame_width, self.frame_height))
                    
                    # Create GStreamer buffer from numpy array
                    data = frame.tobytes()
                    buf = Gst.Buffer.new_allocate(None, len(data), None)
                    buf.fill(0, data)
                    
                    # Set buffer timestamp and duration
                    buf.pts = timestamp
                    buf.duration = Gst.SECOND // 30  # Assuming 30 fps
                    timestamp += buf.duration
                    
                    # Push buffer to appsrc
                    result = src.emit("push-buffer", buf)
                    if result != Gst.FlowReturn.OK:
                        print(f"Error pushing buffer to external stream: {result}")
                    
                    # Report stats periodically
                    frame_count += 1
                    if frame_count % 100 == 0:
                        now = time.time()
                        elapsed = now - last_report_time
                        fps = 100 / elapsed if elapsed > 0 else 0
                        print(f"External stream: sent {frame_count} frames, {fps:.1f} fps")
                        last_report_time = now
                
                # Control frame rate
                time.sleep(1/30)  # Aim for 30 fps
        except Exception as e:
            print(f"Error in external frame sender: {e}")
        finally:
            # Send EOS to pipeline
            if self.external_sending and src is not None:
                src.emit("end-of-stream")
            print("External frame sender stopped")
    
    def _on_internal_new_sample(self, sink: Gst.Element) -> Gst.FlowReturn:
        """Callback for new sample from internal camera receiver pipeline."""
        try:
            # Get sample from appsink
            sample = sink.emit("pull-sample")
            if sample:
                # Get buffer from sample
                buf = sample.get_buffer()
                
                # Get data from buffer
                success, map_info = buf.map(Gst.MapFlags.READ)
                if success:
                    # Convert to numpy array
                    data = map_info.data
                    
                    # Get caps and structure
                    caps = sample.get_caps()
                    structure = caps.get_structure(0)
                    
                    # Get width and height
                    width = structure.get_value("width")
                    height = structure.get_value("height")
                    
                    # Create numpy array from data
                    frame = np.ndarray(
                        shape=(height, width, 3),
                        dtype=np.uint8,
                        buffer=data
                    )
                    
                    # Store frame
                    with self.lock:
                        self.received_internal_frame = frame.copy()
                    
                    # Call callback if registered
                    if self.on_internal_frame_received:
                        self.on_internal_frame_received(frame)
                    
                    # Clean up
                    buf.unmap(map_info)
                    
                return Gst.FlowReturn.OK
        except Exception as e:
            print(f"Error processing internal frame: {e}")
        return Gst.FlowReturn.ERROR
    
    def _on_external_new_sample(self, sink: Gst.Element) -> Gst.FlowReturn:
        """Callback for new sample from external camera receiver pipeline."""
        try:
            # Get sample from appsink
            sample = sink.emit("pull-sample")
            if sample:
                # Get buffer from sample
                buf = sample.get_buffer()
                
                # Get data from buffer
                success, map_info = buf.map(Gst.MapFlags.READ)
                if success:
                    # Convert to numpy array
                    data = map_info.data
                    
                    # Get caps and structure
                    caps = sample.get_caps()
                    structure = caps.get_structure(0)
                    
                    # Get width and height
                    width = structure.get_value("width")
                    height = structure.get_value("height")
                    
                    # Create numpy array from data
                    frame = np.ndarray(
                        shape=(height, width, 3),
                        dtype=np.uint8,
                        buffer=data
                    )
                    
                    # Store frame
                    with self.lock:
                        self.received_external_frame = frame.copy()
                    
                    # Call callback if registered
                    if self.on_external_frame_received:
                        self.on_external_frame_received(frame)
                    
                    # Clean up
                    buf.unmap(map_info)
                    
                return Gst.FlowReturn.OK
        except Exception as e:
            print(f"Error processing external frame: {e}")
        return Gst.FlowReturn.ERROR


# Test function to run the streamer standalone
def test_gstreamer_streamer():
    """Test the GStreamer streamer by displaying sent and received frames."""
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
    
    # Initialize GStreamer streamer with loopback address for testing
    streamer = GStreamerVideoStreamer(camera_manager, "127.0.0.1")
    streamer.start()
    
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
            internal_frame = streamer.get_received_internal_frame()
            external_frame = streamer.get_received_external_frame()
            
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
        streamer.stop()
        camera_manager.stop()


# Run test if executed directly
if __name__ == "__main__":
    test_gstreamer_streamer()
