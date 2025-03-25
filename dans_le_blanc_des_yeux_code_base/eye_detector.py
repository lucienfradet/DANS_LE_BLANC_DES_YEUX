import cv2
import numpy as np
import threading
import time
from shared_variables import local_osc, update_local_osc

class EyeDetector:
    def __init__(self, camera_index=0, detection_interval=0.1):
        self.camera_index = camera_index
        self.detection_interval = detection_interval
        self.running = False
        self.thread = None
        self.cap = None
        
        # Eye detection parameters
        self.eyes_detected = False
        self.detection_stability = 0  # Counter for stable detection
        self.required_stability = 5   # Number of consecutive detections needed
        
        # Load eye cascade classifier
        self.eye_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_eye.xml')
        
        # Region of interest (ROI) - the slit area
        # These values should be adjusted based on your camera and setup
        self.roi_y = 200  # Y-coordinate start of ROI
        self.roi_height = 100  # Height of ROI
        
        # Latest processed frame
        self.processed_frame = None
        self.frame_lock = threading.Lock()
    
    def start(self):
        """Start eye detection in a separate thread"""
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._detection_loop)
            self.thread.daemon = True
            self.thread.start()
            print("Eye detector started")
    
    def stop(self):
        """Stop eye detection"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)
        if self.cap and self.cap.isOpened():
            self.cap.release()
        print("Eye detector stopped")
    
    def _detection_loop(self):
        """Main detection loop running in thread"""
        self.cap = cv2.VideoCapture(self.camera_index)
        
        # Check if camera opened successfully
        if not self.cap.isOpened():
            print("Error: Could not open camera.")
            self.running = False
            return
        
        while self.running:
            # Read frame from camera
            ret, frame = self.cap.read()
            if not ret:
                print("Error: Failed to capture image")
                time.sleep(0.1)
                continue
            
            # Convert to grayscale for processing
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            
            # Define the slit region of interest
            roi_gray = gray[self.roi_y:self.roi_y+self.roi_height, :]
            
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
                print("Eyes detected")
            elif self.detection_stability == 0 and self.eyes_detected:
                self.eyes_detected = False
                # Update shared state
                data = local_osc.copy()
                data["eyes_detected"] = False
                update_local_osc(data)
                print("Eyes lost")
            
            # Process frame for displaying (outline detected eyes)
            display_frame = frame.copy()
            
            # Draw rectangle around the slit ROI
            cv2.rectangle(
                display_frame, 
                (0, self.roi_y), 
                (display_frame.shape[1], self.roi_y + self.roi_height), 
                (0, 255, 0), 
                2
            )
            
            # Draw rectangles around detected eyes
            for (x, y, w, h) in eyes:
                cv2.rectangle(
                    display_frame, 
                    (x, y + self.roi_y),  # Adjust y-coordinate back to full frame
                    (x + w, y + h + self.roi_y), 
                    (255, 0, 0), 
                    2
                )
            
            # Store processed frame for display
            with self.frame_lock:
                self.processed_frame = display_frame
            
            # Process at specified interval
            time.sleep(self.detection_interval)
    
    def get_processed_frame(self):
        """Get the latest processed frame with eye detection visualization"""
        with self.frame_lock:
            if self.processed_frame is not None:
                return self.processed_frame.copy()
            return None
    
    def get_ir_slit_frame(self):
        """Get the IR camera slit only (for overlay)"""
        with self.frame_lock:
            if self.processed_frame is not None:
                # Extract just the slit area
                slit = self.processed_frame[self.roi_y:self.roi_y+self.roi_height, :]
                
                # Apply dithering effect for artistic look
                gray = cv2.cvtColor(slit, cv2.COLOR_BGR2GRAY)
                _, dithered = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY)
                dithered_bgr = cv2.cvtColor(dithered, cv2.COLOR_GRAY2BGR)
                
                return dithered_bgr
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
        eye_detector = EyeDetector()
    return eye_detector

if __name__ == "__main__":
    # Test the eye detector
    detector = get_eye_detector()
    detector.start()
    
    try:
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
