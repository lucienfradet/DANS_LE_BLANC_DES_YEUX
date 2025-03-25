"""
Camera manager for the Dans le Blanc des Yeux installation.
Handles initialization and access to both internal and external cameras.
"""

import time
import threading
import cv2
from picamera2 import Picamera2
from typing import Dict, Optional, Tuple, List

class CameraManager:
    """Manages multiple camera sources for the installation."""
    
    def __init__(self, internal_camera_id: int = 0, external_picam: bool = True):
        self.internal_camera_id = internal_camera_id
        self.use_external_picam = external_picam
        
        # Camera objects
        self.internal_camera = None
        self.external_camera = None
        
        # Latest frames
        self.internal_frame = None
        self.external_frame = None
        
        # Threading
        self.running = False
        self.threads = []
        self.lock = threading.Lock()
        
        # Frame dimensions
        self.frame_width = 640
        self.frame_height = 480
        self.frame_rate = 30
        
        print("Camera manager initialized")
    
    def start(self) -> bool:
        """Initialize and start all cameras."""
        print("Starting camera manager...")
        self.running = True
        
        # Start internal camera
        if not self._start_internal_camera():
            print("Failed to start internal camera")
            # Continue anyway, as we might still have the external camera
        
        # Start external camera
        if not self._start_external_camera():
            print("Failed to start external camera")
            # Continue anyway, as we might still have the internal camera
        
        # Start capture threads if any camera is available
        if self.internal_camera is not None or self.external_camera is not None:
            self._start_capture_threads()
            return True
        else:
            print("No cameras available")
            self.running = False
            return False
    
    def stop(self) -> None:
        """Stop all cameras and release resources."""
        print("Stopping camera manager...")
        self.running = False
        
        # Wait for threads to finish
        for thread in self.threads:
            thread.join(timeout=1.0)
        
        # Release internal camera
        if self.internal_camera is not None:
            self.internal_camera.release()
            self.internal_camera = None
        
        # No need to explicitly release Picamera2, just stop it
        if self.external_camera is not None and isinstance(self.external_camera, Picamera2):
            self.external_camera.stop()
            self.external_camera = None
        elif self.external_camera is not None:
            self.external_camera.release()
            self.external_camera = None
        
        print("Camera manager stopped")
    
    def get_internal_frame(self) -> Optional[cv2.Mat]:
        """Get the latest frame from the internal camera."""
        with self.lock:
            return self.internal_frame.copy() if self.internal_frame is not None else None
    
    def get_external_frame(self) -> Optional[cv2.Mat]:
        """Get the latest frame from the external camera."""
        with self.lock:
            return self.external_frame.copy() if self.external_frame is not None else None
    
    def is_internal_camera_available(self) -> bool:
        """Check if internal camera is available."""
        return self.internal_camera is not None
    
    def is_external_camera_available(self) -> bool:
        """Check if external camera is available."""
        return self.external_camera is not None
    
    def _start_internal_camera(self) -> bool:
        """Initialize and start the internal camera."""
        try:
            print(f"Starting internal camera (ID: {self.internal_camera_id})...")
            self.internal_camera = cv2.VideoCapture(self.internal_camera_id)
            
            # Set resolution and fps
            self.internal_camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.frame_width)
            self.internal_camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.frame_height)
            self.internal_camera.set(cv2.CAP_PROP_FPS, self.frame_rate)
            
            # Check if camera opened successfully
            if not self.internal_camera.isOpened():
                print("Failed to open internal camera")
                self.internal_camera = None
                return False
            
            # Read a test frame
            ret, frame = self.internal_camera.read()
            if not ret or frame is None:
                print("Failed to read from internal camera")
                self.internal_camera.release()
                self.internal_camera = None
                return False
            
            print("Internal camera started successfully")
            return True
        except Exception as e:
            print(f"Error starting internal camera: {e}")
            if self.internal_camera is not None:
                self.internal_camera.release()
                self.internal_camera = None
            return False
    
    def _start_external_camera(self) -> bool:
        """Initialize and start the external camera."""
        try:
            if self.use_external_picam:
                print("Starting external PiCamera...")
                # Initialize PiCamera
                self.external_camera = Picamera2()
                
                # Configure camera
                config = self.external_camera.create_preview_configuration(
                    main={"size": (self.frame_width, self.frame_height), "format": "RGB888"},
                    controls={"FrameRate": self.frame_rate}
                )
                self.external_camera.configure(config)
                
                # Start camera
                self.external_camera.start()
                
                # Test capture
                try:
                    test_frame = self.external_camera.capture_array()
                    if test_frame is None:
                        raise Exception("Failed to capture test frame")
                    print("External PiCamera started successfully")
                    return True
                except Exception as e:
                    print(f"Failed to capture from external PiCamera: {e}")
                    self.external_camera.stop()
                    self.external_camera = None
                    return False
            else:
                # Try to use a second USB camera as external camera
                print("Starting external USB camera...")
                self.external_camera = cv2.VideoCapture(1)  # Usually the second camera
                
                # Set resolution and fps
                self.external_camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.frame_width)
                self.external_camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.frame_height)
                self.external_camera.set(cv2.CAP_PROP_FPS, self.frame_rate)
                
                # Check if camera opened successfully
                if not self.external_camera.isOpened():
                    print("Failed to open external USB camera")
                    self.external_camera = None
                    return False
                
                # Read a test frame
                ret, frame = self.external_camera.read()
                if not ret or frame is None:
                    print("Failed to read from external USB camera")
                    self.external_camera.release()
                    self.external_camera = None
                    return False
                
                print("External USB camera started successfully")
                return True
        except Exception as e:
            print(f"Error starting external camera: {e}")
            if self.external_camera is not None:
                if isinstance(self.external_camera, Picamera2):
                    self.external_camera.stop()
                else:
                    self.external_camera.release()
                self.external_camera = None
            return False
    
    def _start_capture_threads(self) -> None:
        """Start threads to continuously capture frames from cameras."""
        if self.internal_camera is not None:
            internal_thread = threading.Thread(target=self._internal_capture_loop)
            internal_thread.daemon = True
            internal_thread.start()
            self.threads.append(internal_thread)
        
        if self.external_camera is not None:
            external_thread = threading.Thread(target=self._external_capture_loop)
            external_thread.daemon = True
            external_thread.start()
            self.threads.append(external_thread)
    
    def _internal_capture_loop(self) -> None:
        """Continuously capture frames from the internal camera."""
        print("Internal camera capture thread started")
        
        while self.running and self.internal_camera is not None:
            try:
                ret, frame = self.internal_camera.read()
                if ret and frame is not None:
                    with self.lock:
                        self.internal_frame = frame
                else:
                    print("Failed to read from internal camera")
                    # Wait a bit before retrying
                    time.sleep(0.1)
            except Exception as e:
                print(f"Error in internal camera capture: {e}")
                time.sleep(1.0)  # Longer sleep on error
    
    def _external_capture_loop(self) -> None:
        """Continuously capture frames from the external camera."""
        print("External camera capture thread started")
        
        while self.running and self.external_camera is not None:
            try:
                if isinstance(self.external_camera, Picamera2):
                    # PiCamera capture
                    frame = self.external_camera.capture_array()
                    if frame is not None:
                        # Convert from BGR to RGB if needed
                        if frame.shape[2] == 3:  # If it has 3 channels
                            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        with self.lock:
                            self.external_frame = frame
                else:
                    # USB camera capture
                    ret, frame = self.external_camera.read()
                    if ret and frame is not None:
                        with self.lock:
                            self.external_frame = frame
                    else:
                        print("Failed to read from external camera")
                        # Wait a bit before retrying
                        time.sleep(0.1)
            except Exception as e:
                print(f"Error in external camera capture: {e}")
                time.sleep(1.0)  # Longer sleep on error


# Test function to run the camera manager standalone
def test_camera_manager():
    """Test the camera manager by displaying frames from both cameras."""
    import cv2
    
    # Initialize camera manager
    camera_manager = CameraManager()
    if not camera_manager.start():
        print("Failed to start camera manager")
        return
    
    try:
        while True:
            # Get frames
            internal_frame = camera_manager.get_internal_frame()
            external_frame = camera_manager.get_external_frame()
            
            # Display internal frame
            if internal_frame is not None:
                cv2.imshow("Internal Camera", internal_frame)
            
            # Display external frame
            if external_frame is not None:
                cv2.imshow("External Camera", external_frame)
            
            # Exit on 'q' key
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    except KeyboardInterrupt:
        print("Test interrupted by user")
    finally:
        # Clean up
        camera_manager.stop()
        cv2.destroyAllWindows()


# Run test if executed directly
if __name__ == "__main__":
    test_camera_manager()
