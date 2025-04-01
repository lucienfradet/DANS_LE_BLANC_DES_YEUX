"""
Raspberry Pi Camera Manager for the Dans le Blanc des Yeux installation.
Specifically designed for Pi 5 with dedicated ribbon-cable cameras.
"""

import time
import threading
import cv2
import numpy as np
from typing import Dict, Optional, Tuple, List

class CameraManager:
    """Manages multiple Pi Camera modules connected via ribbon cables."""
    
    def __init__(
        self,
        internal_camera_id: int = 0,
        external_camera_id: int = 1,
        disable_missing: bool = True,
        internal_frame_width: int = 640,
        internal_frame_height: int = 480,
        external_frame_width: int = 1000,
        external_frame_height: int = 500,
        enable_autofocus: bool = True
    ):
        self.internal_camera_id = internal_camera_id
        self.external_camera_id = external_camera_id
        self.disable_missing = disable_missing  # Whether to continue if cameras are missing
        
        # Camera dimensions
        self.internal_frame_width = internal_frame_width
        self.internal_frame_height = internal_frame_height
        self.external_frame_width = external_frame_width
        self.external_frame_height = external_frame_height
        
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
        
        # Frame rate
        self.frame_rate = 30
        
        # Autofocus setting
        self.enable_autofocus = enable_autofocus
        
        # Generate test pattern images for when cameras are unavailable
        self._create_test_frames()
        
        # Check which camera is working and adapt
        # If only one camera works, we'll use it for both roles
        self.use_same_camera_for_both = False
        
        print("Pi Camera manager initialized")
    
    def _create_test_frames(self):
        """Create test pattern frames for when cameras are unavailable."""
        # Internal camera test frame (checkerboard pattern)
        internal_test = np.zeros((self.internal_frame_height, self.internal_frame_width, 3), dtype=np.uint8)
        square_size = 40
        for y in range(0, self.internal_frame_height, square_size):
            for x in range(0, self.internal_frame_width, square_size):
                if ((x // square_size) + (y // square_size)) % 2 == 0:
                    internal_test[y:y+square_size, x:x+square_size] = [0, 0, 128]  # Dark blue
                else:
                    internal_test[y:y+square_size, x:x+square_size] = [0, 0, 64]   # Darker blue
        
        # Add text
        cv2.putText(internal_test, "INTERNAL CAMERA UNAVAILABLE", 
                    (int(self.internal_frame_width/2) - 180, int(self.internal_frame_height/2)), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        
        # External camera test frame (gradient pattern)
        external_test = np.zeros((self.external_frame_height, self.external_frame_width, 3), dtype=np.uint8)
        for y in range(self.external_frame_height):
            color_value = int(255 * y / self.external_frame_height)
            external_test[y, :] = [0, color_value, 0]  # Gradient green
            
        # Add text
        cv2.putText(external_test, "EXTERNAL CAMERA UNAVAILABLE", 
                    (int(self.external_frame_width/2) - 180, int(self.external_frame_height/2)), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        
        # Store test frames
        self.internal_test_frame = internal_test
        self.external_test_frame = external_test
    
    def start(self) -> bool:
        """Initialize and start all cameras."""
        print("Starting Pi camera manager...")
        self.running = True
        
        try:
            # Import PiCamera2 here to avoid errors if it's not installed
            from picamera2 import Picamera2
            
            # Get list of available cameras
            num_cameras = Picamera2.global_camera_info()
            print(f"Found {len(num_cameras)} cameras")
            
            # Start internal camera
            internal_started = self._start_internal_camera()
            if not internal_started:
                print("Internal camera not available, using test pattern")
                self.internal_frame = self.internal_test_frame
            
            # Start external camera
            external_started = self._start_external_camera()
            if not external_started:
                print("External camera not available")
                
                # If internal camera works but external doesn't, use internal for both
                if internal_started:
                    print("Using internal camera for both roles")
                    self.use_same_camera_for_both = True
                    self.external_frame = self.internal_frame.copy()
                else:
                    # Neither camera is working
                    self.external_frame = self.external_test_frame
            
            # If both cameras failed and we don't allow missing cameras, abort
            if not internal_started and not external_started and not self.disable_missing:
                print("Both cameras failed to start and disable_missing=False")
                self.running = False
                return False
            
            # Start capture threads for available cameras
            if internal_started:
                internal_thread = threading.Thread(target=self._internal_capture_loop)
                internal_thread.daemon = True
                internal_thread.start()
                self.threads.append(internal_thread)
            
            if external_started and not self.use_same_camera_for_both:
                external_thread = threading.Thread(target=self._external_capture_loop)
                external_thread.daemon = True
                external_thread.start()
                self.threads.append(external_thread)
            
            return True
            
        except ImportError:
            print("ERROR: PiCamera2 module not found")
            print("Please install with: pip install picamera2")
            self.running = False
            return False
        
        except Exception as e:
            print(f"Error starting cameras: {e}")
            self.running = False
            return False
    
    def stop(self) -> None:
        """Stop all cameras and release resources."""
        print("Stopping camera manager...")
        self.running = False
        
        # Wait for threads to finish
        for thread in self.threads:
            thread.join(timeout=1.0)
        
        # Stop internal camera
        if self.internal_camera is not None:
            try:
                self.internal_camera.stop()
            except Exception as e:
                print(f"Error stopping internal camera: {e}")
            self.internal_camera = None
        
        # Stop external camera if it's not the same as internal
        if self.external_camera is not None and not self.use_same_camera_for_both:
            try:
                self.external_camera.stop()
            except Exception as e:
                print(f"Error stopping external camera: {e}")
            self.external_camera = None
        
        print("Camera manager stopped")
    
    def get_internal_frame(self) -> Optional[np.ndarray]:
        """Get the latest frame from the internal camera."""
        with self.lock:
            return self.internal_frame.copy() if self.internal_frame is not None else None
    
    def get_external_frame(self) -> Optional[np.ndarray]:
        """Get the latest frame from the external camera."""
        with self.lock:
            # If using same camera for both, return internal frame
            if self.use_same_camera_for_both:
                return self.internal_frame.copy() if self.internal_frame is not None else None
            else:
                return self.external_frame.copy() if self.external_frame is not None else None
    
    def is_internal_camera_available(self) -> bool:
        """Check if internal camera is available."""
        return self.internal_camera is not None
    
    def is_external_camera_available(self) -> bool:
        """Check if external camera is available."""
        return self.external_camera is not None or self.use_same_camera_for_both
    
    def _start_internal_camera(self) -> bool:
        """Initialize and start the internal camera."""
        try:
            from picamera2 import Picamera2
            
            print(f"Starting internal camera (ID: {self.internal_camera_id})...")
            
            # Handle case where there might not be enough cameras
            camera_info = Picamera2.global_camera_info()
            if len(camera_info) <= self.internal_camera_id:
                print(f"Camera ID {self.internal_camera_id} not available")
                print(f"Available cameras: {len(camera_info)}")
                return False
            
            # Initialize PiCamera
            self.internal_camera = Picamera2(self.internal_camera_id)
            
            # Configure camera with internal camera dimensions
            config = self.internal_camera.create_preview_configuration(
                main={"size": (self.internal_frame_width, self.internal_frame_height), "format": "RGB888"},
                controls={"FrameRate": self.frame_rate}
            )
            self.internal_camera.configure(config)
            
            # Start camera
            self.internal_camera.start()
            
            # Test capture
            test_frame = self.internal_camera.capture_array()
            if test_frame is None:
                print("Failed to capture test frame from internal camera")
                self.internal_camera.stop()
                self.internal_camera = None
                return False
            
            # Store initial frame
            with self.lock:
                self.internal_frame = test_frame
                
            print("Internal camera started successfully")
            return True
            
        except Exception as e:
            print(f"Error starting internal camera: {e}")
            if self.internal_camera is not None:
                try:
                    self.internal_camera.stop()
                except:
                    pass
                self.internal_camera = None
            return False
    
    def _start_external_camera(self) -> bool:
        """Initialize and start the external camera."""
        try:
            from picamera2 import Picamera2
            from picamera2.controls import Controls
            
            print(f"Starting external camera (ID: {self.external_camera_id})...")
            
            # Get available cameras
            camera_info = Picamera2.global_camera_info()
            if len(camera_info) <= self.external_camera_id:
                print(f"Camera ID {self.external_camera_id} not available")
                print(f"Available cameras: {len(camera_info)}")
                return False
            
            # Check if we're trying to use the same camera twice
            if self.external_camera_id == self.internal_camera_id:
                print("WARNING: External camera ID is the same as internal camera ID")
                print("Using the same camera for both roles")
                self.use_same_camera_for_both = True
                return False  # Return false so we don't create two instances
            
            # Initialize PiCamera
            self.external_camera = Picamera2(self.external_camera_id)
            
            # Prepare camera controls with frame rate
            camera_controls = {"FrameRate": self.frame_rate}
            
            # Configure camera with external camera dimensions
            config = self.external_camera.create_preview_configuration(
                main={"size": (self.external_frame_width, self.external_frame_height), "format": "RGB888"},
                controls=camera_controls
            )
            self.external_camera.configure(config)
            
            # Start camera
            self.external_camera.start()

            time.sleep(1)

            # Set up autofocus if enabled
            if self.enable_autofocus:
                try:
                    self.external_camera.set_controls({"AfMode": 2 ,"AfTrigger": 0})
                    print("Enabling continuous autofocus for external camera")
                except Exception as af_error:
                    print(f"Could not enable autofocus: {af_error}")
            
            # Test capture
            test_frame = self.external_camera.capture_array()
            if test_frame is None:
                print("Failed to capture test frame from external camera")
                self.external_camera.stop()
                self.external_camera = None
                return False
            
            # Store initial frame
            with self.lock:
                self.external_frame = test_frame
                
            print("External camera started successfully")
            return True
            
        except Exception as e:
            print(f"Error starting external camera: {e}")
            if self.external_camera is not None:
                try:
                    self.external_camera.stop()
                except:
                    pass
                self.external_camera = None
            return False
    
    def _internal_capture_loop(self) -> None:
        """Continuously capture frames from the internal camera."""
        print("Internal camera capture thread started")
        
        frame_count = 0
        last_error_time = 0
        
        while self.running and self.internal_camera is not None:
            try:
                # Capture frame
                frame = self.internal_camera.capture_array()
                
                if frame is not None:
                    # Store the frame
                    with self.lock:
                        self.internal_frame = frame
                        
                        # If using same camera for both, also update external frame
                        if self.use_same_camera_for_both:
                            self.external_frame = frame.copy()
                    
                    # Log status periodically
                    frame_count += 1
                    if frame_count % 100 == 0:
                        print(f"Internal camera: captured {frame_count} frames")
                else:
                    current_time = time.time()
                    if current_time - last_error_time > 5:
                        print("Failed to capture frame from internal camera")
                        last_error_time = current_time
                    
                    with self.lock:
                        if self.internal_frame is None:
                            self.internal_frame = self.internal_test_frame
                    
                    time.sleep(0.1)
            except Exception as e:
                current_time = time.time()
                if current_time - last_error_time > 5:
                    print(f"Error capturing from internal camera: {e}")
                    last_error_time = current_time
                
                with self.lock:
                    self.internal_frame = self.internal_test_frame
                
                time.sleep(0.5)
    
    def _external_capture_loop(self) -> None:
        """Continuously capture frames from the external camera."""
        print("External camera capture thread started")
        
        frame_count = 0
        last_error_time = 0
        
        while self.running and self.external_camera is not None:
            try:
                # Capture frame
                frame = self.external_camera.capture_array()
                
                if frame is not None:
                    # Store the frame
                    with self.lock:
                        self.external_frame = frame
                    
                    # Log status periodically
                    frame_count += 1
                    if frame_count % 100 == 0:
                        print(f"External camera: captured {frame_count} frames")
                else:
                    current_time = time.time()
                    if current_time - last_error_time > 5:
                        print("Failed to capture frame from external camera")
                        last_error_time = current_time
                    
                    with self.lock:
                        if self.external_frame is None:
                            self.external_frame = self.external_test_frame
                    
                    time.sleep(0.1)
            except Exception as e:
                current_time = time.time()
                if current_time - last_error_time > 5:
                    print(f"Error capturing from external camera: {e}")
                    last_error_time = current_time
                
                with self.lock:
                    self.external_frame = self.external_test_frame
                
                time.sleep(0.5)


# Test function to run the camera manager standalone
def test_camera_manager():
    """Test the camera manager by displaying frames from both cameras."""
    import cv2
    
    # Initialize camera manager with custom dimensions and autofocus
    camera_manager = CameraManager(
        internal_frame_width=640,
        internal_frame_height=480,
        external_frame_width=800,
        external_frame_height=600,
        enable_autofocus=True
    )
    
    if not camera_manager.start():
        print("Failed to start camera manager")
        return
    
    try:
        while True:
            # Get frames
            internal_frame = camera_manager.get_internal_frame()
            external_frame = camera_manager.get_external_frame()
            
            # Create combined display
            if internal_frame is not None and external_frame is not None:
                # Resize to same height if necessary
                h1, w1 = internal_frame.shape[:2]
                h2, w2 = external_frame.shape[:2]
                
                # Calculate new dimensions to make heights equal
                if h1 != h2:
                    if h1 > h2:
                        w2 = int(w2 * (h1 / h2))
                        external_frame = cv2.resize(external_frame, (w2, h1))
                    else:
                        w1 = int(w1 * (h2 / h1))
                        internal_frame = cv2.resize(internal_frame, (w1, h2))
                
                # Stack side by side
                combined = np.hstack((internal_frame, external_frame))
                
                # Add labels
                cv2.putText(combined, "Internal Camera", (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                cv2.putText(combined, "External Camera", (w1 + 10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                
                # Display
                cv2.imshow("Camera Test", combined)
            else:
                # Display available frames individually
                if internal_frame is not None:
                    cv2.imshow("Internal Camera", internal_frame)
                if external_frame is not None:
                    cv2.imshow("External Camera", external_frame)
            
            # Exit on 'q' key
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    except KeyboardInterrupt:
        print("Test interrupted by user")
    except Exception as e:
        print(f"Error in test: {e}")
    finally:
        # Clean up
        camera_manager.stop()
        cv2.destroyAllWindows()


# Run test if executed directly
if __name__ == "__main__":
    test_camera_manager()
