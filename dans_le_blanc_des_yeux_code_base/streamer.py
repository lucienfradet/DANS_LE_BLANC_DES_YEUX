"""
GStreamer-based video streaming for the art installation.
Handles sending and receiving video streams between two installations.
"""

import gi
import os
import threading
import time
import logging
import socket
import numpy as np
import cv2

# Set up logging
logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('video_streamer')

# Import camera utilities
from camera_utils import (
    find_working_camera,
    create_camera_capture,
    create_camera_capture_gstreamer,
    get_gstreamer_pipeline_str,
    GSTREAMER_AVAILABLE
)

from shared_variables import config

# Import GStreamer if available
try:
    gi.require_version('Gst', '1.0')
    gi.require_version('GstApp', '1.0')
    from gi.repository import Gst, GObject, GstApp
    Gst.init(None)
    GSTREAMER_INITIALIZED = True
    logger.info("GStreamer initialized successfully")
except (ImportError, ValueError) as e:
    GSTREAMER_INITIALIZED = False
    logger.error(f"Failed to initialize GStreamer: {e}")

class VideoStreamer:
    def __init__(self):
        # Configuration
        self.remote_ip = config['ip']['pi-ip']
        self.streaming_port = 5000
        self.receiving_port = 5001
        
        # Pipeline objects
        self.sender_pipeline = None
        self.receiver_pipeline = None
        
        # State tracking
        self.streaming = False
        self.receiving = False
        
        # Frame buffer for received frames
        self.latest_frame = None
        self.frame_lock = threading.Lock()
        
        # Create a blank frame for fallback
        self.blank_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        self.add_text_to_frame(self.blank_frame, "No remote feed available")
        
        # Camera source
        self.camera_index = None  # Will be auto-detected
        
        # Check if we can use GStreamer
        self.use_gstreamer = GSTREAMER_INITIALIZED
        if not self.use_gstreamer:
            logger.warning("GStreamer not available. Falling back to simplified streaming.")
            # Will use a fallback method
    
    def start_streaming(self):
        """Start streaming video to the remote installation"""
        if self.streaming:
            logger.info("Already streaming")
            return
        
        self.streaming = True
        
        if self.use_gstreamer:
            # Use GStreamer pipeline for streaming
            self._start_gstreamer_streaming()
        else:
            # Use fallback method (e.g., OpenCV)
            logger.warning("GStreamer not available. Using fallback streaming method")
            self._start_fallback_streaming()
    
    def stop_streaming(self):
        """Stop streaming video"""
        if not self.streaming:
            return
        
        self.streaming = False
        
        if self.use_gstreamer and self.sender_pipeline:
            # Stop GStreamer pipeline
            self.sender_pipeline.set_state(Gst.State.NULL)
            self.sender_pipeline = None
            logger.info("Stopped GStreamer streaming")
        else:
            # Stop fallback streaming
            logger.info("Stopped fallback streaming")
    
    def start_receiving(self):
        """Start receiving video from the remote installation"""
        if self.receiving:
            logger.info("Already receiving")
            return
        
        self.receiving = True
        
        if self.use_gstreamer:
            # Use GStreamer pipeline for receiving
            self._start_gstreamer_receiving()
        else:
            # Use fallback method
            logger.warning("GStreamer not available. Using fallback receiving method")
            self._start_fallback_receiving()
    
    def stop_receiving(self):
        """Stop receiving video"""
        if not self.receiving:
            return
        
        self.receiving = False
        
        if self.use_gstreamer and self.receiver_pipeline:
            # Stop GStreamer pipeline
            self.receiver_pipeline.set_state(Gst.State.NULL)
            self.receiver_pipeline = None
            logger.info("Stopped GStreamer receiving")
        else:
            # Stop fallback receiving
            logger.info("Stopped fallback receiving")
    
    def get_latest_frame(self):
        """Get the latest received frame"""
        with self.frame_lock:
            if self.latest_frame is not None:
                return self.latest_frame.copy()
            else:
                return self.blank_frame.copy()
    
    def cleanup(self):
        """Clean up resources"""
        self.stop_streaming()
        self.stop_receiving()
        logger.info("VideoStreamer cleaned up")
    
    def _start_gstreamer_streaming(self):
        """Start streaming using GStreamer"""
        try:
            # Auto-detect camera if needed
            if self.camera_index is None:
                self.camera_index = find_working_camera()
                if self.camera_index is None:
                    logger.error("No working camera found")
                    return
            
            # Create sender pipeline
            pipeline_elements = []
            
            # First, try to find an appropriate video source
            # Try available source elements in order of preference
            source_element = None
            
            # Check if we can create a v4l2src pipeline
            try:
                factory = Gst.ElementFactory.find("v4l2src")
                if factory:
                    source_element = f"v4l2src device=/dev/video{self.camera_index}"
                    logger.info(f"Using v4l2src with device /dev/video{self.camera_index}")
            except Exception as e:
                logger.warning(f"Error checking for v4l2src: {e}")
            
            # If v4l2src failed, try libcamerasrc
            if not source_element:
                try:
                    factory = Gst.ElementFactory.find("libcamerasrc")
                    if factory:
                        source_element = "libcamerasrc"
                        logger.info("Using libcamerasrc")
                except Exception as e:
                    logger.warning(f"Error checking for libcamerasrc: {e}")
            
            # If both failed, try videotestsrc as last resort
            if not source_element:
                try:
                    factory = Gst.ElementFactory.find("videotestsrc")
                    if factory:
                        source_element = "videotestsrc"
                        logger.info("Using videotestsrc (test pattern) as fallback")
                except Exception as e:
                    logger.warning(f"Error checking for videotestsrc: {e}")
            
            # If we still don't have a source, we can't continue
            if not source_element:
                logger.error("No suitable video source found")
                return
            
            # Build the pipeline
            pipeline_elements.append(source_element)
            pipeline_elements.append("! videoconvert ! videoscale")
            pipeline_elements.append("! video/x-raw,width=640,height=480,framerate=30/1")
            pipeline_elements.append("! videoconvert ! x264enc tune=zerolatency speed-preset=ultrafast")
            pipeline_elements.append("! rtph264pay ! udpsink host=" + self.remote_ip + " port=" + str(self.streaming_port))
            
            # Join the pipeline elements
            pipeline_str = " ".join(pipeline_elements)
            logger.info(f"Creating sender pipeline: {pipeline_str}")
            
            # Create and start the pipeline
            self.sender_pipeline = Gst.parse_launch(pipeline_str)
            if self.sender_pipeline:
                self.sender_pipeline.set_state(Gst.State.PLAYING)
                logger.info("Started GStreamer streaming")
            else:
                logger.error("Failed to create sender pipeline")
        except Exception as e:
            logger.error(f"Error in GStreamer streaming: {e}")
            self.streaming = False
    
    def _start_gstreamer_receiving(self):
        """Start receiving using GStreamer"""
        try:
            # Create receiver pipeline
            pipeline_elements = []
            
            # Build the pipeline for receiving
            pipeline_elements.append(f"udpsrc port={self.receiving_port} caps=\"application/x-rtp,media=video,encoding-name=H264\"")
            pipeline_elements.append("! rtph264depay ! h264parse ! avdec_h264")
            pipeline_elements.append("! videoconvert ! video/x-raw,format=BGR")
            pipeline_elements.append("! appsink name=sink emit-signals=true sync=false")
            
            # Join the pipeline elements
            pipeline_str = " ".join(pipeline_elements)
            logger.info(f"Creating receiver pipeline: {pipeline_str}")
            
            # Create the pipeline
            self.receiver_pipeline = Gst.parse_launch(pipeline_str)
            
            if self.receiver_pipeline:
                # Get the appsink element
                appsink = self.receiver_pipeline.get_by_name("sink")
                
                # Connect to the new-sample signal
                appsink.connect("new-sample", self._on_new_sample)
                
                # Start the pipeline
                self.receiver_pipeline.set_state(Gst.State.PLAYING)
                logger.info("Started GStreamer receiving")
            else:
                logger.error("Failed to create receiver pipeline")
        except Exception as e:
            logger.error(f"Error in GStreamer receiving: {e}")
            self.receiving = False
    
    def _on_new_sample(self, appsink):
        """Callback for new video samples from GStreamer"""
        try:
            # Get the sample
            sample = appsink.pull_sample()
            if sample:
                # Get the buffer
                buffer = sample.get_buffer()
                
                # Get the data
                success, map_info = buffer.map(Gst.MapFlags.READ)
                if success:
                    # Convert to numpy array
                    data = map_info.data
                    
                    # Get caps and structure
                    caps = sample.get_caps()
                    structure = caps.get_structure(0)
                    
                    # Get dimensions
                    width = structure.get_value("width")
                    height = structure.get_value("height")
                    
                    # Create numpy array from the data
                    frame = np.ndarray(
                        shape=(height, width, 3),
                        dtype=np.uint8,
                        buffer=data
                    )
                    
                    # Store the frame
                    with self.frame_lock:
                        self.latest_frame = frame
                    
                    # Clean up
                    buffer.unmap(map_info)
            
            # Return OK to continue
            return Gst.FlowReturn.OK
        except Exception as e:
            logger.error(f"Error in new-sample handler: {e}")
            return Gst.FlowReturn.ERROR
    
    def _start_fallback_streaming(self):
        """Fallback method for streaming using OpenCV and sockets"""
        # This would be implemented using OpenCV and sockets
        # For demo purposes, just create a thread that sends frames
        def streaming_thread():
            try:
                # Find a working camera
                if self.camera_index is None:
                    self.camera_index = find_working_camera()
                
                # Create a camera capture
                cap = create_camera_capture(self.camera_index)
                
                if cap is None:
                    logger.error("Failed to create camera capture")
                    self.streaming = False
                    return
                
                logger.info("Fallback streaming started")
                
                # In a real implementation, this would send frames over the network
                # Here, we'll just simulate streaming by sleeping
                while self.streaming:
                    ret, frame = cap.read()
                    if ret:
                        # In a real implementation, encode and send the frame
                        # For demo, just log that we got a frame
                        logger.debug("Captured frame for streaming")
                    else:
                        logger.warning("Failed to capture frame")
                    
                    time.sleep(0.033)  # ~30 FPS
                
                # Clean up
                cap.release()
                logger.info("Fallback streaming stopped")
            except Exception as e:
                logger.error(f"Error in fallback streaming: {e}")
                self.streaming = False
        
        # Start the streaming thread
        threading.Thread(target=streaming_thread, daemon=True).start()
    
    def _start_fallback_receiving(self):
        """Fallback method for receiving using OpenCV and sockets"""
        # This would be implemented using OpenCV and sockets
        # For demo purposes, just create a thread that generates fake frames
        def receiving_thread():
            try:
                logger.info("Fallback receiving started")
                
                # Create a socket to listen on
                # In a real implementation, this would receive frames from the network
                # Here, we'll just simulate receiving by creating frames
                
                count = 0
                while self.receiving:
                    # Create a simulated frame
                    frame = np.zeros((480, 640, 3), dtype=np.uint8)
                    # Add some dynamic content
                    count += 1
                    self.add_text_to_frame(
                        frame, 
                        f"Simulated Remote Feed - Frame {count}"
                    )
                    
                    # Store the frame
                    with self.frame_lock:
                        self.latest_frame = frame
                    
                    time.sleep(0.033)  # ~30 FPS
                
                logger.info("Fallback receiving stopped")
            except Exception as e:
                logger.error(f"Error in fallback receiving: {e}")
                self.receiving = False
        
        # Start the receiving thread
        threading.Thread(target=receiving_thread, daemon=True).start()
    
    def add_text_to_frame(self, frame, text):
        """Helper to add text to a frame"""
        # Get text size
        font = cv2.FONT_HERSHEY_SIMPLEX
        text_size = cv2.getTextSize(text, font, 1, 2)[0]
        
        # Calculate position to center text
        text_x = (frame.shape[1] - text_size[0]) // 2
        text_y = (frame.shape[0] + text_size[1]) // 2
        
        # Add text to frame
        cv2.putText(
            frame,
            text,
            (text_x, text_y),
            font,
            1,
            (255, 255, 255),
            2
        )

# Singleton instance
video_streamer = None

def get_video_streamer():
    """Get or create the VideoStreamer instance"""
    global video_streamer
    if video_streamer is None:
        video_streamer = VideoStreamer()
    return video_streamer

if __name__ == "__main__":
    # Test the video streamer
    streamer = get_video_streamer()
    
    # Create display window
    cv2.namedWindow("Remote Feed", cv2.WINDOW_NORMAL)
    
    # Start receiving
    streamer.start_receiving()
    
    try:
        # Show received frames
        while True:
            frame = streamer.get_latest_frame()
            cv2.imshow("Remote Feed", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    except KeyboardInterrupt:
        pass
    finally:
        streamer.cleanup()
        cv2.destroyAllWindows()
