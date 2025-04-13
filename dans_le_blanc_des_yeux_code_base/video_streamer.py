"""
Video streaming module for the Dans le Blanc des Yeux installation.
Handles sending and receiving video streams between devices using H.265 encoding over GStreamer.

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
gi.require_version('GstApp', '1.0')
from gi.repository import Gst, GstApp, GLib, GObject

from system_state import system_state
from camera_manager import CameraManager

# Port configuration
INTERNAL_STREAM_PORT = 5000  # Port for internal camera stream
EXTERNAL_STREAM_PORT = 5001  # Port for external camera stream

# Initialize GStreamer
Gst.init(None)

class VideoStreamer:
    """Handles video streaming between devices using H.265 encoding and GStreamer."""
    
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
        
        # GStreamer pipelines
        self.internal_sender_pipeline = None
        self.external_sender_pipeline = None
        self.internal_receiver_pipeline = None
        self.external_receiver_pipeline = None
        
        # GStreamer elements for feeding frames
        self.internal_appsrc = None
        self.external_appsrc = None
        
        # GStreamer appsinks for receiving frames
        self.internal_appsink = None
        self.external_appsink = None
        
        # Bus polling threads
        self.internal_sender_poll_thread = None
        self.external_sender_poll_thread = None
        self.internal_receiver_poll_thread = None
        self.external_receiver_poll_thread = None
        
        # Appsink polling threads
        self.internal_receiver_pull_thread = None
        self.external_receiver_pull_thread = None
        
        # Callbacks
        self.on_internal_frame_received = None
        self.on_external_frame_received = None
        
        # Register as observer for state changes
        system_state.add_observer(self._on_state_change)
        
        print(f"Video streamer initialized with remote IP: {remote_ip}")
    
    def start(self) -> bool:
        """Start the video streaming system by creating GStreamer pipelines."""
        print("Starting video streamer...")
        self.running = True
        
        # Create all pipelines (in paused state)
        success = (
            self._create_internal_receiver_pipeline() and
            self._create_external_receiver_pipeline() and
            self._create_internal_sender_pipeline() and
            self._create_external_sender_pipeline()
        )
        
        if not success:
            self.stop()
            return False
        
        # Start receiver pipelines (always active)
        self._start_receiver_pipelines()
        
        # Check initial state to see if we need to start streaming right away
        self._update_streaming_based_on_state()
        
        print("Video streamer started")
        return True
    
    def stop(self) -> None:
        """Stop all streaming and release resources."""
        print("Stopping video streamer...")
        self.running = False
        
        # Stop all pipelines
        self._stop_all_pipelines()
        
        # Wait for threads to finish
        for thread in self.threads:
            if thread and thread.is_alive():
                thread.join(timeout=2.0)
        
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
            self._pause_all_sender_pipelines()
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
            self._pause_all_sender_pipelines()
    
    def _create_internal_sender_pipeline(self) -> bool:
        """Create GStreamer pipeline for sending internal camera frames."""
        if not self.camera_manager.is_internal_camera_available():
            print("Internal camera not available for creating pipeline")
            return False
        
        try:
            # Create pipeline in paused state
            pipeline_str = (
                f"appsrc name=src format=time is-live=true do-timestamp=true ! "
                f"videoconvert ! video/x-raw,format=I420,width={self.frame_width},height={self.frame_height} ! "
                f"x265enc bitrate=2000 tune=zerolatency speed-preset=superfast ! "
                f"rtph265pay config-interval=1 ! "
                f"udpsink host={self.remote_ip} port={INTERNAL_STREAM_PORT} sync=false"
            )
            
            self.internal_sender_pipeline = Gst.parse_launch(pipeline_str)
            
            # Get appsrc element
            self.internal_appsrc = self.internal_sender_pipeline.get_by_name("src")
            self.internal_appsrc.set_property("caps", Gst.Caps.from_string(
                f"video/x-raw,format=BGR,width={self.frame_width},height={self.frame_height},framerate=30/1"
            ))
            
            # Start bus polling thread for this pipeline
            self._start_bus_polling_thread(
                self.internal_sender_pipeline, 
                "internal_sender", 
                lambda: self.internal_sender_poll_thread
            )
            
            # Set pipeline to paused state
            self.internal_sender_pipeline.set_state(Gst.State.PAUSED)
            print(f"Created internal sender pipeline (paused)")
            return True
        
        except Exception as e:
            print(f"Failed to create internal sender pipeline: {e}")
            return False
    
    def _create_external_sender_pipeline(self) -> bool:
        """Create GStreamer pipeline for sending external camera frames."""
        if not self.camera_manager.is_external_camera_available():
            print("External camera not available for creating pipeline")
            return False
        
        try:
            # Create pipeline in paused state
            pipeline_str = (
                f"appsrc name=src format=time is-live=true do-timestamp=true ! "
                f"videoconvert ! video/x-raw,format=I420,width={self.frame_width},height={self.frame_height} ! "
                f"x265enc bitrate=2000 tune=zerolatency speed-preset=superfast ! "
                f"rtph265pay config-interval=1 ! "
                f"udpsink host={self.remote_ip} port={EXTERNAL_STREAM_PORT} sync=false"
            )
            
            self.external_sender_pipeline = Gst.parse_launch(pipeline_str)
            
            # Get appsrc element
            self.external_appsrc = self.external_sender_pipeline.get_by_name("src")
            self.external_appsrc.set_property("caps", Gst.Caps.from_string(
                f"video/x-raw,format=BGR,width={self.frame_width},height={self.frame_height},framerate=30/1"
            ))
            
            # Start bus polling thread for this pipeline
            self._start_bus_polling_thread(
                self.external_sender_pipeline, 
                "external_sender", 
                lambda: self.external_sender_poll_thread
            )
            
            # Set pipeline to paused state
            self.external_sender_pipeline.set_state(Gst.State.PAUSED)
            print(f"Created external sender pipeline (paused)")
            return True
        
        except Exception as e:
            print(f"Failed to create external sender pipeline: {e}")
            return False
    
    def _create_internal_receiver_pipeline(self) -> bool:
        """Create GStreamer pipeline for receiving internal camera frames."""
        try:
            # Create pipeline in null state
            pipeline_str = (
                f"udpsrc port={INTERNAL_STREAM_PORT} caps=\"application/x-rtp,media=video,encoding-name=H265,payload=96\" ! "
                f"rtph265depay ! h265parse ! avdec_h265 ! "
                f"videoconvert ! video/x-raw,format=BGR ! "
                f"appsink name=sink max-buffers=1 drop=true sync=false"
            )
            
            self.internal_receiver_pipeline = Gst.parse_launch(pipeline_str)
            
            # Get appsink element
            self.internal_appsink = self.internal_receiver_pipeline.get_by_name("sink")
            self.internal_appsink.set_property("emit-signals", False)  # We'll manually pull samples
            
            # Start bus polling thread for this pipeline
            self._start_bus_polling_thread(
                self.internal_receiver_pipeline, 
                "internal_receiver", 
                lambda: self.internal_receiver_poll_thread
            )
            
            print(f"Created internal receiver pipeline")
            return True
        
        except Exception as e:
            print(f"Failed to create internal receiver pipeline: {e}")
            return False
    
    def _create_external_receiver_pipeline(self) -> bool:
        """Create GStreamer pipeline for receiving external camera frames."""
        try:
            # Create pipeline in null state
            pipeline_str = (
                f"udpsrc port={EXTERNAL_STREAM_PORT} caps=\"application/x-rtp,media=video,encoding-name=H265,payload=96\" ! "
                f"rtph265depay ! h265parse ! avdec_h265 ! "
                f"videoconvert ! video/x-raw,format=BGR ! "
                f"appsink name=sink max-buffers=1 drop=true sync=false"
            )
            
            self.external_receiver_pipeline = Gst.parse_launch(pipeline_str)
            
            # Get appsink element
            self.external_appsink = self.external_receiver_pipeline.get_by_name("sink")
            self.external_appsink.set_property("emit-signals", False)  # We'll manually pull samples
            
            # Start bus polling thread for this pipeline
            self._start_bus_polling_thread(
                self.external_receiver_pipeline, 
                "external_receiver", 
                lambda: self.external_receiver_poll_thread
            )
            
            print(f"Created external receiver pipeline")
            return True
        
        except Exception as e:
            print(f"Failed to create external receiver pipeline: {e}")
            return False
    
    def _start_bus_polling_thread(self, pipeline, name, thread_getter):
        """Start a thread that polls for messages on a pipeline's bus."""
        def poll_bus():
            bus = pipeline.get_bus()
            while self.running:
                message = bus.timed_pop_filtered(
                    100 * Gst.MSECOND,  # 100ms timeout
                    Gst.MessageType.ERROR | Gst.MessageType.WARNING | Gst.MessageType.EOS
                )
                if message:
                    self._handle_pipeline_message(message, name)
        
        thread = threading.Thread(target=poll_bus, name=f"{name}_bus_poll")
        thread.daemon = True
        thread.start()
        
        if name == "internal_sender":
            self.internal_sender_poll_thread = thread
        elif name == "external_sender":
            self.external_sender_poll_thread = thread
        elif name == "internal_receiver":
            self.internal_receiver_poll_thread = thread
        elif name == "external_receiver":
            self.external_receiver_poll_thread = thread
        
        self.threads.append(thread)
    
    def _start_receiver_pipelines(self) -> None:
        """Start the receiver pipelines and their sample pulling threads."""
        # Start internal receiver pipeline and pulling thread
        if self.internal_receiver_pipeline:
            self.internal_receiver_pipeline.set_state(Gst.State.PLAYING)
            print("Started internal receiver pipeline")
            
            # Start thread to pull samples
            self.internal_receiver_pull_thread = threading.Thread(
                target=self._pull_internal_receiver_samples,
                name="internal_receiver_pull"
            )
            self.internal_receiver_pull_thread.daemon = True
            self.internal_receiver_pull_thread.start()
            self.threads.append(self.internal_receiver_pull_thread)
        
        # Start external receiver pipeline and pulling thread
        if self.external_receiver_pipeline:
            self.external_receiver_pipeline.set_state(Gst.State.PLAYING)
            print("Started external receiver pipeline")
            
            # Start thread to pull samples
            self.external_receiver_pull_thread = threading.Thread(
                target=self._pull_external_receiver_samples,
                name="external_receiver_pull"
            )
            self.external_receiver_pull_thread.daemon = True
            self.external_receiver_pull_thread.start()
            self.threads.append(self.external_receiver_pull_thread)
    
    def _pull_internal_receiver_samples(self):
        """Continuously pull samples from the internal receiver's appsink."""
        while self.running:
            if self.internal_appsink:
                sample = self.internal_appsink.try_pull_sample(100 * Gst.MSECOND)  # 100ms timeout
                if sample:
                    self._process_internal_sample(sample)
            time.sleep(0.01)  # Small sleep to prevent CPU hogging
    
    def _pull_external_receiver_samples(self):
        """Continuously pull samples from the external receiver's appsink."""
        while self.running:
            if self.external_appsink:
                sample = self.external_appsink.try_pull_sample(100 * Gst.MSECOND)  # 100ms timeout
                if sample:
                    self._process_external_sample(sample)
            time.sleep(0.01)  # Small sleep to prevent CPU hogging
    
    def _process_internal_sample(self, sample):
        """Process a sample from the internal receiver pipeline."""
        frame = self._sample_to_numpy(sample)
        if frame is not None:
            with self.lock:
                self.received_internal_frame = frame
            
            # Call callback if registered
            if self.on_internal_frame_received:
                self.on_internal_frame_received(frame)
    
    def _process_external_sample(self, sample):
        """Process a sample from the external receiver pipeline."""
        frame = self._sample_to_numpy(sample)
        if frame is not None:
            with self.lock:
                self.received_external_frame = frame
            
            # Call callback if registered
            if self.on_external_frame_received:
                self.on_external_frame_received(frame)
    
    def _sample_to_numpy(self, sample):
        """Convert a GStreamer sample to a numpy array."""
        try:
            buffer = sample.get_buffer()
            caps = sample.get_caps()
            
            # Get buffer info
            success, map_info = buffer.map(Gst.MapFlags.READ)
            if not success:
                return None
            
            # Get caps structure
            structure = caps.get_structure(0)
            
            # Get dimensions
            width = structure.get_value("width")
            height = structure.get_value("height")
            
            # Create numpy array from buffer
            frame = np.ndarray(
                shape=(height, width, 3),
                dtype=np.uint8,
                buffer=map_info.data
            )
            
            # Make a copy of the data
            result = frame.copy()
            
            # Unmap the buffer
            buffer.unmap(map_info)
            
            return result
        
        except Exception as e:
            print(f"Error converting sample to numpy: {e}")
            return None
    
    def _start_internal_stream(self) -> bool:
        """Start streaming the internal camera to the remote device."""
        if self.internal_sending:
            return True
        
        if not self.camera_manager.is_internal_camera_available():
            print("Internal camera not available")
            return False
        
        try:
            # Start the feed thread if not already running
            if not self.internal_sending:
                self.internal_sending = True
                thread = threading.Thread(target=self._internal_feed_loop)
                thread.daemon = True
                thread.start()
                self.threads.append(thread)
            
            # Set pipeline to playing state
            if self.internal_sender_pipeline:
                self.internal_sender_pipeline.set_state(Gst.State.PLAYING)
                print(f"Started internal camera stream to {self.remote_ip}:{INTERNAL_STREAM_PORT}")
                return True
            return False
        
        except Exception as e:
            print(f"Failed to start internal camera stream: {e}")
            self.internal_sending = False
            return False
    
    def _start_external_stream(self) -> bool:
        """Start streaming the external camera to the remote device."""
        if self.external_sending:
            return True
        
        if not self.camera_manager.is_external_camera_available():
            print("External camera not available")
            return False
        
        try:
            # Start the feed thread if not already running
            if not self.external_sending:
                self.external_sending = True
                thread = threading.Thread(target=self._external_feed_loop)
                thread.daemon = True
                thread.start()
                self.threads.append(thread)
            
            # Set pipeline to playing state
            if self.external_sender_pipeline:
                self.external_sender_pipeline.set_state(Gst.State.PLAYING)
                print(f"Started external camera stream to {self.remote_ip}:{EXTERNAL_STREAM_PORT}")
                return True
            return False
        
        except Exception as e:
            print(f"Failed to start external camera stream: {e}")
            self.external_sending = False
            return False
    
    def _stop_internal_stream(self) -> None:
        """Stop streaming the internal camera."""
        if self.internal_sending:
            self.internal_sending = False
            
            if self.internal_sender_pipeline:
                self.internal_sender_pipeline.set_state(Gst.State.PAUSED)
            
            print("Stopped internal camera stream")
    
    def _stop_external_stream(self) -> None:
        """Stop streaming the external camera."""
        if self.external_sending:
            self.external_sending = False
            
            if self.external_sender_pipeline:
                self.external_sender_pipeline.set_state(Gst.State.PAUSED)
            
            print("Stopped external camera stream")
    
    def _pause_all_sender_pipelines(self) -> None:
        """Pause all sender pipelines."""
        self._stop_internal_stream()
        self._stop_external_stream()
    
    def _stop_all_pipelines(self) -> None:
        """Stop all GStreamer pipelines and clean up."""
        # Stop sender pipelines
        self._pause_all_sender_pipelines()
        
        # Stop and clean up all pipelines
        for pipeline in [self.internal_sender_pipeline, self.external_sender_pipeline,
                         self.internal_receiver_pipeline, self.external_receiver_pipeline]:
            if pipeline:
                pipeline.set_state(Gst.State.NULL)
        
        print("All pipelines stopped")
    
    def _internal_feed_loop(self) -> None:
        """Feed frames from the internal camera to the GStreamer pipeline."""
        try:
            while self.running and self.internal_sending:
                # Get frame from camera manager
                frame = self.camera_manager.get_internal_frame()
                
                if frame is not None and self.internal_appsrc:
                    # Push frame to GStreamer pipeline
                    self._push_frame_to_appsrc(frame, self.internal_appsrc)
                
                # Control frame rate
                time.sleep(0.033)  # ~30 fps
        except Exception as e:
            if self.running and self.internal_sending:
                print(f"Error in internal feed loop: {e}")
        finally:
            print("Internal feed loop stopped")
    
    def _external_feed_loop(self) -> None:
        """Feed frames from the external camera to the GStreamer pipeline."""
        try:
            while self.running and self.external_sending:
                # Get frame from camera manager
                frame = self.camera_manager.get_external_frame()
                
                if frame is not None and self.external_appsrc:
                    # Push frame to GStreamer pipeline
                    self._push_frame_to_appsrc(frame, self.external_appsrc)
                
                # Control frame rate
                time.sleep(0.033)  # ~30 fps
        except Exception as e:
            if self.running and self.external_sending:
                print(f"Error in external feed loop: {e}")
        finally:
            print("External feed loop stopped")
    
    def _push_frame_to_appsrc(self, frame: np.ndarray, appsrc: GstApp.AppSrc) -> None:
        """Push a frame to a GStreamer AppSrc element."""
        if frame.shape[0] != self.frame_height or frame.shape[1] != self.frame_width:
            frame = cv2.resize(frame, (self.frame_width, self.frame_height))
        
        # Create GStreamer buffer from numpy array
        buffer_size = frame.size
        gst_buffer = Gst.Buffer.new_allocate(None, buffer_size, None)
        
        # Fill buffer with frame data
        if gst_buffer:
            gst_buffer.fill(0, frame.tobytes())
            # Push buffer to appsrc
            appsrc.emit("push-buffer", gst_buffer)
    
    def _handle_pipeline_message(self, message: Gst.Message, pipeline_name: str) -> None:
        """Handle GStreamer pipeline messages."""
        if not message:
            return
            
        t = message.type
        
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"Error from {pipeline_name}: {err}, {debug}")
        
        elif t == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            print(f"Warning from {pipeline_name}: {warn}, {debug}")
        
        elif t == Gst.MessageType.EOS:
            print(f"End of stream from {pipeline_name}")


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
