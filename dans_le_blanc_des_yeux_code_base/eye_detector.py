import cv2
import numpy as np
import threading
import time
import logging
from shared_variables import local_osc, update_local_osc

# Import the new camera utilities
from camera_utils import (
    find_working_camera,
    create_camera_capture,
    create_camera_capture_gstreamer
)

# Set up logging
logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('eye_detector')

class EyeDetector:
    def __init__(self, camera_index=None, detection_interval=0.1):
        self.camera_index = camera_index  # If None, will auto-detect
        self.detection_interval = detection_interval
        self.running = False
        self.thread = None
        self.cap = None
        
        # Eye detection parameters
        self.eyes_detected = False
        self.detection_stability = 0  # Counter for stable detection
        self.required_stability = 5   # Number of consecutive detections needed
        
        # Load eye cascade classifier
        try:
            self.eye_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_eye.xml')
            if self.eye_cascade.empty():
                logger.error("Failed to load eye cascade classifier")
                # Try an alternate path as fallback
                alt_path = '/usr/local/share/opencv4/haarcascades/haarcascade_eye.xml'
                if os.path.exists(alt_path):
                    self.eye_cascade = cv2.CascadeClassifier(alt_path)
                    logger.info(f"Loaded eye cascade from alternate path: {alt_path}")
        except Exception as e:
            logger.error(f"Error loading eye cascade: {e}")
        
        # Region of interest (ROI) - the slit area
        # These values should be adjusted based on your camera and setup
        self.roi_y = 200  # Y-coordinate start of ROI
        self.roi_height = 100  # Height of ROI
        
        # Latest processed frame
        self.processed_frame = None
        self.blank_frame = np.zeros((480, 640, 3), dtype=np.uint8)  # Fallback frame
        self.frame_lock = threading.Lock()
        
        # Camera error tracking
        self.camera_errors = 0
        self.max_camera_errors = 10
        self.camera_reconnect_delay = 3  # seconds
    
    def start(self):
        """Start eye detection in a separate thread"""
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._detection_loop)
            self.thread.daemon = True
            self.thread.start()
            logger.info("Eye detector started")
    
    def stop(self):
        """Stop eye detection"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)
        if self.cap and hasattr(self.cap, 'release'):
            self.cap.release()
        logger.info("Eye detector stopped")
    
    def _initialize_camera(self):
        """Initialize camera with auto-detection and fallbacks"""
        # If camera_index is None, auto-detect
        if self.camera_index is None:
            logger.info("Auto-detecting camera...")
            self.camera_index = find_working_camera()
            if self.camera_index is None:
                logger.error("No working camera found")
                return False
        
        # Try GStreamer first
        logger.info(f"Attempting to create camera capture with GStreamer (index {self.camera_index})...")
        self.cap = create_camera_capture_gstreamer(self.camera_index)
        
        # If GStreamer failed, try standard capture
        if self.cap is None or not self.cap.isOpened():
            logger.info(f"Attempting standard camera capture (index {self.camera_index})...")
            self.cap = create_camera_capture(self.camera_index)
        
        # Final check if camera is open
        if self.cap is None or not self.cap.isOpened():
            logger.error(f"Failed to open camera with index {self.camera_index}")
            return False
        
        # Read a test frame to make sure camera works
        ret, frame = self.cap.read()
        if not ret or frame is None or frame.size == 0:
            logger.error("Camera opened but failed to read a valid frame")
            if self.cap:
                self.cap.release()
            return False
        
        logger.info(f"Successfully initialized camera with index {self.camera_index}")
        return True
    
    def _detection_loop(self):
        """Main detection loop running in thread"""
        camera_initialized = self._initialize_camera()
        
        if not camera_initialized:
            logger.error("Failed to initialize camera, eye detector will use blank frames")
            # Use a blank frame as the processed frame
            with self.frame_lock:
                self.processed_frame = self.blank_frame.copy()
        
        consecutive_failures = 0
        
        while self.running:
            try:
                # If camera isn't initialized or has failed, try to reinitialize
                if not camera_initialized and consecutive_failures % 30 == 0:  # Try every ~3 seconds
                    logger.info("Attempting to reinitialize camera...")
                    camera_initialized = self._initialize_camera()
                    if camera_initialized:
                        consecutive_failures = 0
                
                # If camera is initialized, read frame
                if camera_initialized and self.cap and self.cap.isOpened():
                    ret, frame = self.cap.read()
                    
                    if not ret or frame is None or frame.size == 0:
                        consecutive_failures += 1
                        logger.warning(f"Failed to read frame (failure {consecutive_failures})")
                        
                        # If too many consecutive failures, try to reinitialize camera
                        if consecutive_failures >= 10:
                            logger.error("Too many consecutive frame read failures, reinitializing camera")
                            if self.cap:
                                self.cap.release()
                            camera_initialized = self._initialize_camera()
                            consecutive_failures = 0
                        
                        # Use a blank frame for this iteration
                        frame = self.blank_frame.copy()
                    else:
                        # Reset failure counter on successful frame read
                        consecutive_failures = 0
                else:
                    # Use blank frame if camera not initialized
                    frame = self.blank_frame.copy()
                
                # Process the frame (either real or blank)
                self._process_frame(frame)
                
            except Exception as e:
                logger.error(f"Error in detection loop: {e}")
                consecutive_failures += 1
            
            # Process at specified interval
            time.sleep(self.detection_interval)
    
    def _process_frame(self, frame):
        """Process a frame for eye detection"""
        try:
            # Create a copy for display
            display_frame = frame.copy()
            
            # Convert to grayscale for processing
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            
            # Define the slit region of interest
            # Ensure roi_y and height are within frame bounds
            max_y = max(0, min(self.roi_y, gray.shape[0] - 1))
            max_height = max(1, min(self.roi_height, gray.shape[0] - max_y))
            roi_gray = gray[max_y:max_y+max_height, :]
            
            # Only attempt detection if the ROI is valid and eye_cascade is available
            eyes = []
            if roi_gray.size > 0 and self.eye_cascade and not self.eye_cascade.empty():
                # Detect eyes in the ROI
                eyes = self.eye_cascade.detectMultiScale(
                    roi_gray,
                    scaleFactor=1.1,
                    minNeighbors=5,
                    minSize=(30, 30)
                )
            
            # Update detection status
            eyes_visible = len(eyes) > 0
            
            # Apply stability check for detection
            if eyes_visible:
                self.detection_stability += 1
            else:
                self.detection_stability = 0
            
            # Update detection state if stable enough
            if self.detection_stability >= self.required_stability and not self.eyes_detected:
                self.eyes_detected = True
                # Update shared state
                data = local_osc.copy()
                data["eyes_detected"] = True
                update_local_osc(data)
                logger.info("Eyes detected")
            elif self.detection_stability == 0 and self.eyes_detected:
                self.eyes_detected = False
                # Update shared state
                data = local_osc.copy()
                data["eyes_detected"] = False
                update_local_osc(data)
                logger.info("Eyes lost")
            
            # Draw rectangle around the slit ROI
            cv2.rectangle(
                display_frame, 
                (0, max_y), 
                (display_frame.shape[1], max_y + max_height), 
                (0, 255, 0), 
                2
            )
            
            # Draw rectangles around detected eyes
            for (x, y, w, h) in eyes:
                cv2.rectangle(
                    display_frame, 
                    (x, y + max_y),  # Adjust y-coordinate back to full frame
                    (x + w, y + h + max_y), 
                    (255, 0, 0), 
                    2
                )
                
            # Add detection status text
            status_text = f"Eyes: {'Detected' if self.eyes_detected else 'Not Detected'}"
            cv2.putText(
                display_frame,
                status_text,
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 0, 255) if self.eyes_detected else (0, 255, 0),
                2
            )
            
            # Store processed frame for display
            with self.frame_lock:
                self.processed_frame = display_frame
                
        except Exception as e:
            logger.error(f"Error processing frame: {e}")
            # Use a blank frame with error message as fallback
            error_frame = self.blank_frame.copy()
            cv2.putText(
                error_frame,
                f"Error: {str(e)}",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 0, 255),
                2
            )
            with self.frame_lock:
                self.processed_frame = error_frame
    
    def get_processed_frame(self):
        """Get the latest processed frame with eye detection visualization"""
        with self.frame_lock:
            if self.processed_frame is not None:
                return self.processed_frame.copy()
            return self.blank_frame.copy()
    
    def get_ir_slit_frame(self):
        """Get the IR camera slit only (for overlay)"""
        with self.frame_lock:
            if self.processed_frame is not None:
                try:
                    # Extract just the slit area
                    max_y = max(0, min(self.roi_y, self.processed_frame.shape[0] - 1))
                    max_height = max(1, min(self.roi_height, self.processed_frame.shape[0] - max_y))
                    slit = self.processed_frame[max_y:max_y+max_height, :]
                    
                    # Apply dithering effect for artistic look
                    gray = cv2.cvtColor(slit, cv2.COLOR_BGR2GRAY)
                    _, dithered = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY)
                    dithered_bgr = cv2.cvtColor(dithered, cv2.COLOR_GRAY2BGR)
                    
                    return dithered_bgr
                except Exception as e:
                    logger.error(f"Error creating slit frame: {e}")
                    return None
            return None
    
    def is_detecting_eyes(self):
        """Check if eyes are currently being detected"""
        return self.eyes_detected

# Singleton instance
eye_detector = None

def get_eye_detector():
    """Get or create the EyeDetector instance"""
    global eye_detector
    if eye_detector is None:
        # Try to auto-detect camera
        eye_detector = EyeDetector(camera_index=None)
    return eye_detector

if __name__ == "__main__":
    # Test the eye detector
    import os
    detector = get_eye_detector()
    detector.start()
    
    try:
        # Create window
        cv2.namedWindow('Eye Detection', cv2.WINDOW_NORMAL)
        cv2.namedWindow('IR Slit', cv2.WINDOW_NORMAL)
        
        while True:
            frame = detector.get_processed_frame()
            if frame is not None:
                cv2.imshow('Eye Detection', frame)
                
                slit = detector.get_ir_slit_frame()
                if slit is not None:
                    cv2.imshow('IR Slit', slit)
                
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
                
    except KeyboardInterrupt:
        pass
    finally:
        detector.stop()
        cv2.destroyAllWindows()
