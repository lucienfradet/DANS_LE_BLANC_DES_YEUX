"""
Camera manager for the Dans le Blanc des Yeux installation.
With special optimizations for Raspberry Pi 5 camera handling.
"""

import time
import threading
import cv2
import numpy as np
import os
import subprocess
from typing import Dict, Optional, Tuple, List
import re

class CameraManager:
    """Manages multiple camera sources for the installation."""
    
    def __init__(self, internal_camera_id: int = 0, external_picam: bool = True, disable_missing: bool = True):
        self.internal_camera_id = internal_camera_id
        self.use_external_picam = external_picam
        self.disable_missing = disable_missing  # Whether to continue if cameras are missing
        
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
        
        # Pi-specific camera IDs
        self.pi_camera_paths = self._get_pi_camera_paths()
        
        # Generate test pattern images for when cameras are unavailable
        self._create_test_frames()
        
        print("Camera manager initialized")
    
    def _get_pi_camera_paths(self) -> Dict[str, str]:
        """Get Raspberry Pi camera device paths."""
        camera_paths = {}
        
        try:
            # Try v4l2-ctl to list devices
            result = subprocess.run(["v4l2-ctl", "--list-devices"], 
                                   stdout=subprocess.PIPE, 
                                   stderr=subprocess.PIPE, 
                                   text=True, 
                                   check=False)
            
            if result.returncode == 0:
                # Parse output to find camera devices
                device_name = None
                lines = result.stdout.strip().split('\n')
                
                for line in lines:
                    if ':' in line:
                        # This is a device name line
                        device_name = line.split(':')[0].strip()
                    elif '/dev/video' in line and device_name:
                        # This is a device path line
                        device_path = line.strip()
                        
                        # For Pi 5, we're interested in the rp1-cfe devices for PiCamera
                        if 'rp1-cfe' in device_name:
                            if 'picamera_main' not in camera_paths:
                                # First rp1-cfe device is main PiCamera
                                camera_paths['picamera_main'] = device_path
                                print(f"Found main PiCamera at {device_path}")
                                continue
                        
                        # Look for USB webcams (usually show up as different device types)
                        if 'usb' in device_name.lower() and 'webcam' not in camera_paths:
                            camera_paths['webcam'] = device_path
                            print(f"Found USB webcam at {device_path}")
                
                # If specific cameras weren't found, try to make best guesses
                if 'picamera_main' not in camera_paths and 'rp1-cfe' in result.stdout:
                    # Extract the first video device for rp1-cfe
                    match = re.search(r'rp1-cfe.*?\n\s+(/dev/video\d+)', result.stdout, re.DOTALL)
                    if match:
                        camera_paths['picamera_main'] = match.group(1)
                        print(f"Found likely PiCamera at {camera_paths['picamera_main']}")
                
                # Find any webcam as a fallback
                if 'webcam' not in camera_paths:
                    # Get any video device not already assigned
                    for line in lines:
                        if '/dev/video' in line:
                            device_path = line.strip()
                            if (device_path not in camera_paths.values() and 
                                'picamera_main' in camera_paths and
                                device_path != camera_paths['picamera_main']):
                                camera_paths['webcam'] = device_path
                                print(f"Found possible webcam at {device_path}")
                                break
        except Exception as e:
            print(f"Error detecting Pi cameras: {e}")
        
        # Fallback to default device paths if nothing was found
        if not camera_paths:
            camera_paths['picamera_main'] = '/dev/video0'
            camera_paths['webcam'] = '/dev/video1'
            print("Using default camera paths: /dev/video0 and /dev/video1")
        
        return camera_paths
    
    def _create_test_frames(self):
        """Create test pattern frames for when cameras are unavailable."""
        # Internal camera test frame (checkerboard pattern)
        internal_test = np.zeros((self.frame_height, self.frame_width, 3), dtype=np.uint8)
        square_size = 40
        for y in range(0, self.frame_height, square_size):
            for x in range(0, self.frame_width, square_size):
                if ((x // square_size) + (y // square_size)) % 2 == 0:
                    internal_test[y:y+square_size, x:x+square_size] = [0, 0, 128]  # Dark blue
                else:
                    internal_test[y:y+square_size, x:x+square_size] = [0, 0, 64]   # Darker blue
        
        # Add text
        cv2.putText(internal_test, "INTERNAL CAMERA UNAVAILABLE", 
                    (int(self.frame_width/2) - 180, int(self.frame_height/2)), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        
        # External camera test frame (gradient pattern)
        external_test = np.zeros((self.frame_height, self.frame_width, 3), dtype=np.uint8)
        for y in range(self.frame_height):
            color_value = int(255 * y / self.frame_height)
            external_test[y, :] = [0, color_value, 0]  # Gradient green
            
        # Add text
        cv2.putText(external_test, "EXTERNAL CAMERA UNAVAILABLE", 
                    (int(self.frame_width/2) - 180, int(self.frame_height/2)), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        
        # Store test frames
        self.internal_test_frame = internal_test
        self.external_test_frame = external_test
    
    def start(self) -> bool:
        """Initialize and start all cameras."""
        print("Starting camera manager...")
        self.running = True
        
        # Start internal camera
        internal_started = self._start_internal_camera()
        if not internal_started:
            print("Internal camera not available, using test pattern")
            self.internal_frame = self.internal_test_frame
        
        # Start external camera
        external_started = self._start_external_camera()
        if not external_started:
            print("External camera not available, using test pattern")
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
        
        if external_started:
            external_thread = threading.Thread(target=self._external_capture_loop)
            external_thread.daemon = True
            external_thread.start()
            self.threads.append(external_thread)
        
        return True
    
    def stop(self) -> None:
        """Stop all cameras and release resources."""
        print("Stopping camera manager...")
        self.running = False
        
        # Wait for threads to finish
        for thread in self.threads:
            thread.join(timeout=1.0)
        
        # Release internal camera
        if self.internal_camera is not None:
            try:
                self.internal_camera.release()
            except Exception as e:
                print(f"Error releasing internal camera: {e}")
            self.internal_camera = None
        
        # Stop external camera
        if self.external_camera is not None:
            try:
                if "picamera2" in str(type(self.external_camera)).lower():
                    # PiCamera2 stop method
                    self.external_camera.stop()
                else:
                    # OpenCV VideoCapture release method
                    self.external_camera.release()
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
            return self.external_frame.copy() if self.external_frame is not None else None
    
    def is_internal_camera_available(self) -> bool:
        """Check if internal camera is available."""
        return self.internal_camera is not None
    
    def is_external_camera_available(self) -> bool:
        """Check if external camera is available."""
        return self.external_camera is not None
    
    def _path_to_index(self, path: str) -> int:
        """Convert a device path to a camera index."""
        try:
            # Extract the number from something like /dev/video0
            match = re.search(r'/dev/video(\d+)', path)
            if match:
                return int(match.group(1))
            return 0  # Default to camera 0 if no match
        except Exception:
            return 0
    
    def _start_internal_camera(self) -> bool:
        """Initialize and start the internal camera."""
        try:
            # Determine which camera to use as internal
            if self.internal_camera_id is not None:
                # User specified a specific ID
                camera_id = self.internal_camera_id
                print(f"Using specified internal camera ID: {camera_id}")
            elif 'webcam' in self.pi_camera_paths:
                # Use webcam if available
                camera_id = self._path_to_index(self.pi_camera_paths['webcam'])
                print(f"Using detected webcam for internal camera: {self.pi_camera_paths['webcam']} (ID: {camera_id})")
            else:
                # Fall back to ID 0
                camera_id = 0
                print(f"No webcam detected, using default internal camera ID: {camera_id}")
            
            print(f"Starting internal camera (ID: {camera_id})...")
            self.internal_camera = cv2.VideoCapture(camera_id)
            
            # Set resolution and fps
            self.internal_camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.frame_width)
            self.internal_camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.frame_height)
            self.internal_camera.set(cv2.CAP_PROP_FPS, self.frame_rate)
            
            # Check if camera opened successfully
            if not self.internal_camera.isOpened():
                print("Failed to open internal camera")
                self.internal_camera.release()
                self.internal_camera = None
                return False
            
            # Read a test frame
            ret, frame = self.internal_camera.read()
            if not ret or frame is None:
                print("Failed to read from internal camera")
                self.internal_camera.release()
                self.internal_camera = None
                return False
            
            # Store initial frame
            with self.lock:
                self.internal_frame = frame
            
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
                # Try to use PiCamera first
                try:
                    # Import PiCamera2 here to avoid errors if it's not installed
                    from picamera2 import Picamera2
                    
                    print("Starting external PiCamera...")
                    
                    # For Pi 5, try to use the camera module device if found
                    if 'picamera_main' in self.pi_camera_paths:
                        camera_id = self._path_to_index(self.pi_camera_paths['picamera_main'])
                        print(f"Using detected PiCamera device: {self.pi_camera_paths['picamera_main']} (ID: {camera_id})")
                    else:
                        camera_id = 0
                        print(f"Using default PiCamera ID: {camera_id}")
                    
                    # Initialize PiCamera with ID
                    self.external_camera = Picamera2(camera_id)
                    
                    # Configure camera
                    config = self.external_camera.create_preview_configuration(
                        main={"size": (self.frame_width, self.frame_height), "format": "RGB888"},
                        controls={"FrameRate": self.frame_rate}
                    )
                    self.external_camera.configure(config)
                    
                    # Start camera
                    self.external_camera.start()
                    
                    # Test capture
                    test_frame = self.external_camera.capture_array()
                    if test_frame is None:
                        raise Exception("Failed to capture test frame")
                    
                    # Store initial frame
                    with self.lock:
                        self.external_frame = test_frame
                        
                    print("External PiCamera started successfully")
                    return True
                    
                except (ImportError, Exception) as e:
                    print(f"Error with PiCamera: {e}")
                    if self.external_camera is not None:
                        try:
                            self.external_camera.stop()
                        except:
                            pass
                        self.external_camera = None
                    
                    # Fall back to USB camera
                    print("Falling back to USB camera as external camera")
                    return self._start_external_usb_camera()
            else:
                # Use USB camera as external
                return self._start_external_usb_camera()
                
        except Exception as e:
            print(f"Error in external camera initialization: {e}")
            if self.external_camera is not None:
                try:
                    if "picamera2" in str(type(self.external_camera)).lower():
                        self.external_camera.stop()
                    else:
                        self.external_camera.release()
                except:
                    pass
                self.external_camera = None
            return False
    
    def _start_external_usb_camera(self) -> bool:
        """Try to initialize a USB camera as the external camera."""
        try:
            print("Starting external USB camera...")
            
            # Try using a different camera than the internal one
            
            # If we're using webcam as internal, try picam for external
            if ('webcam' in self.pi_camera_paths and 
                'picamera_main' in self.pi_camera_paths and
                self._path_to_index(self.pi_camera_paths['webcam']) == self.internal_camera_id):
                camera_id = self._path_to_index(self.pi_camera_paths['picamera_main'])
                print(f"Using PiCamera device as external USB camera: {self.pi_camera_paths['picamera_main']} (ID: {camera_id})")
            # Otherwise try webcam for external
            elif 'webcam' in self.pi_camera_paths:
                camera_id = self._path_to_index(self.pi_camera_paths['webcam'])
                print(f"Using webcam as external camera: {self.pi_camera_paths['webcam']} (ID: {camera_id})")
            # Try standard IDs as fallback
            else:
                # Try ID 1 first, then 0 if that's not the internal camera
                if self.internal_camera_id != 1:
                    camera_id = 1
                else:
                    camera_id = 0
                print(f"Using fallback external camera ID: {camera_id}")
            
            if camera_id == self.internal_camera_id:
                print(f"WARNING: External camera ID ({camera_id}) is the same as internal camera ID.")
                print("Both cameras will show the same image.")
            
            self.external_camera = cv2.VideoCapture(camera_id)
            
            # Set resolution and fps
            self.external_camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.frame_width)
            self.external_camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.frame_height)
            self.external_camera.set(cv2.CAP_PROP_FPS, self.frame_rate)
            
            # Check if camera opened successfully
            if not self.external_camera.isOpened():
                print(f"Failed to open external USB camera with ID {camera_id}")
                self.external_camera.release()
                self.external_camera = None
                return False
            
            # Read a test frame
            ret, frame = self.external_camera.read()
            if not ret or frame is None:
                print(f"Failed to read from external USB camera with ID {camera_id}")
                self.external_camera.release()
                self.external_camera = None
                return False
            
            # Store initial frame
            with self.lock:
                self.external_frame = frame
            
            print(f"External USB camera started successfully with ID {camera_id}")
            return True
            
        except Exception as e:
            print(f"Error starting external USB camera: {e}")
            if self.external_camera is not None:
                self.external_camera.release()
                self.external_camera = None
            return False
    
    def _internal_capture_loop(self) -> None:
        """Continuously capture frames from the internal camera."""
        print("Internal camera capture thread started")
        
        frame_count = 0
        last_error_time = 0
        
        while self.running and self.internal_camera is not None:
            try:
                ret, frame = self.internal_camera.read()
                if ret and frame is not None:
                    with self.lock:
                        self.internal_frame = frame
                    
                    # Log occasional status updates
                    frame_count += 1
                    if frame_count % 100 == 0:
                        print(f"Internal camera: captured {frame_count} frames")
                else:
                    current_time = time.time()
                    # Limit error logging to avoid spamming
                    if current_time - last_error_time > 5.0:
                        print("Failed to read from internal camera")
                        last_error_time = current_time
                    
                    # Use test pattern if camera fails
                    with self.lock:
                        if self.internal_frame is None:
                            self.internal_frame = self.internal_test_frame
                    
                    # Wait a bit before retrying
                    time.sleep(0.2)
            except Exception as e:
                current_time = time.time()
                # Limit error logging
                if current_time - last_error_time > 5.0:
                    print(f"Error in internal camera capture: {e}")
                    last_error_time = current_time
                
                # Use test pattern if camera fails
                with self.lock:
                    self.internal_frame = self.internal_test_frame
                    
                time.sleep(0.5)  # Longer sleep on error
    
    def _external_capture_loop(self) -> None:
        """Continuously capture frames from the external camera."""
        print("External camera capture thread started")
        
        frame_count = 0
        last_error_time = 0
        
        while self.running and self.external_camera is not None:
            try:
                if "picamera2" in str(type(self.external_camera)).lower():
                    # PiCamera capture
                    frame = self.external_camera.capture_array()
                    if frame is not None:
                        # Convert from BGR to RGB if needed
                        if frame.shape[2] == 3:  # If it has 3 channels
                            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        with self.lock:
                            self.external_frame = frame
                        
                        # Log occasional status updates
                        frame_count += 1
                        if frame_count % 100 == 0:
                            print(f"External camera: captured {frame_count} frames")
                    else:
                        current_time = time.time()
                        # Limit error logging
                        if current_time - last_error_time > 5.0:
                            print("Failed to capture from external PiCamera")
                            last_error_time = current_time
                        
                        # Use test pattern if camera fails
                        with self.lock:
                            if self.external_frame is None:
                                self.external_frame = self.external_test_frame
                        
                        time.sleep(0.2)
                else:
                    # USB camera capture
                    ret, frame = self.external_camera.read()
                    if ret and frame is not None:
                        with self.lock:
                            self.external_frame = frame
                        
                        # Log occasional status updates
                        frame_count += 1
                        if frame_count % 100 == 0:
                            print(f"External camera: captured {frame_count} frames")
                    else:
                        current_time = time.time()
                        # Limit error logging
                        if current_time - last_error_time > 5.0:
                            print("Failed to read from external USB camera")
                            last_error_time = current_time
                        
                        # Use test pattern if camera fails
                        with self.lock:
                            if self.external_frame is None:
                                self.external_frame = self.external_test_frame
                        
                        time.sleep(0.2)
            except Exception as e:
                current_time = time.time()
                # Limit error logging
                if current_time - last_error_time > 5.0:
                    print(f"Error in external camera capture: {e}")
                    last_error_time = current_time
                
                # Use test pattern if camera fails
                with self.lock:
                    self.external_frame = self.external_test_frame
                    
                time.sleep(0.5)  # Longer sleep on error


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
