"""
Camera utility functions for the 'Dans le Blanc des Yeux' art installation.
Provides robust camera detection and initialization.
"""

import cv2
import os
import subprocess
import time
import logging
import gi

# Set up logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('camera_utils')

# Initialize GStreamer if available
try:
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    Gst.init(None)
    GSTREAMER_AVAILABLE = True
    logger.info("GStreamer initialized successfully")
except (ImportError, ValueError) as e:
    GSTREAMER_AVAILABLE = False
    logger.warning(f"GStreamer initialization failed: {e}")

def get_available_cameras(max_devices=10):
    """
    Discover available camera devices on the system.
    
    Args:
        max_devices (int): Maximum number of devices to check
        
    Returns:
        list: List of available camera device paths
    """
    camera_devices = []
    
    # Check /dev/video* devices
    for i in range(max_devices):
        device_path = f"/dev/video{i}"
        if os.path.exists(device_path):
            camera_devices.append(device_path)
    
    # If we found devices, log them
    if camera_devices:
        logger.info(f"Found camera devices: {camera_devices}")
    else:
        logger.warning("No camera devices found in /dev/video*")
        
    return camera_devices

def check_raspberry_pi_camera():
    """
    Check if Raspberry Pi camera module is enabled.
    
    Returns:
        bool: True if Pi camera is enabled, False otherwise
    """
    try:
        # Check if vcgencmd exists (Raspberry Pi utility)
        result = subprocess.run(["vcgencmd", "get_camera"], 
                                capture_output=True, text=True, check=False)
        
        if "detected=1" in result.stdout:
            logger.info("Raspberry Pi camera module detected")
            return True
        else:
            logger.warning("Raspberry Pi camera module not detected or not enabled")
            return False
    except (subprocess.SubprocessError, FileNotFoundError):
        logger.info("Not running on a Raspberry Pi or vcgencmd not available")
        return False

def test_camera_device(device_path_or_index):
    """
    Test if a camera device can be opened successfully.
    
    Args:
        device_path_or_index: Either a device path (str) or camera index (int)
        
    Returns:
        bool: True if camera can be accessed, False otherwise
    """
    # If device_path_or_index is a string path, convert to index
    if isinstance(device_path_or_index, str) and device_path_or_index.startswith('/dev/video'):
        try:
            index = int(device_path_or_index.replace('/dev/video', ''))
        except ValueError:
            logger.error(f"Invalid device path: {device_path_or_index}")
            return False
    else:
        index = device_path_or_index
    
    logger.info(f"Testing camera at index {index}")
    
    # Try to open camera
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        logger.warning(f"Failed to open camera at index {index}")
        return False
    
    # Try to read a frame
    ret, frame = cap.read()
    cap.release()
    
    if ret and frame is not None and frame.size > 0:
        logger.info(f"Successfully accessed camera at index {index}")
        return True
    else:
        logger.warning(f"Could read from camera at index {index}, but got invalid frame")
        return False

def find_working_camera(max_devices=10):
    """
    Find the first working camera device.
    
    Args:
        max_devices (int): Maximum number of devices to check
        
    Returns:
        int or None: Camera index if found, None otherwise
    """
    # Try specific indices first (common camera indices)
    for idx in [0, 1, 2]:
        if test_camera_device(idx):
            return idx
    
    # If not found, try device paths
    devices = get_available_cameras(max_devices)
    for device in devices:
        if test_camera_device(device):
            # Extract index from device path
            try:
                index = int(device.replace('/dev/video', ''))
                return index
            except ValueError:
                continue
    
    # No working camera found
    logger.error("No working camera found after checking all available devices")
    return None

def create_camera_capture(camera_index=None, resolution=(640, 480), retry_attempts=3, retry_delay=1):
    """
    Create a camera capture object with retries and fallback.
    
    Args:
        camera_index (int, optional): Camera index to use. If None, will find a working camera.
        resolution (tuple): Desired resolution as (width, height)
        retry_attempts (int): Number of retry attempts
        retry_delay (float): Delay between retries in seconds
        
    Returns:
        cv2.VideoCapture or None: Camera capture object if successful, None otherwise
    """
    # If no camera index specified, find one
    if camera_index is None:
        camera_index = find_working_camera()
        if camera_index is None:
            return None
    
    # Try to open camera with retries
    for attempt in range(retry_attempts):
        logger.info(f"Attempting to open camera {camera_index} (attempt {attempt+1}/{retry_attempts})")
        
        cap = cv2.VideoCapture(camera_index)
        
        # Configure camera resolution
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, resolution[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, resolution[1])
        
        if cap.isOpened():
            # Verify by reading a frame
            ret, frame = cap.read()
            if ret and frame is not None and frame.size > 0:
                actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                logger.info(f"Successfully opened camera {camera_index} at resolution {actual_width}x{actual_height}")
                return cap
            else:
                cap.release()
                logger.warning(f"Camera {camera_index} opened but frame read failed")
        
        if attempt < retry_attempts - 1:
            logger.info(f"Retrying in {retry_delay} seconds...")
            time.sleep(retry_delay)
    
    logger.error(f"Failed to open camera {camera_index} after {retry_attempts} attempts")
    return None

def get_gstreamer_pipeline_str(camera_index=0, width=640, height=480, fps=30):
    """
    Generate GStreamer pipeline string for camera capture, with fallbacks.
    
    Args:
        camera_index (int): Camera device index
        width (int): Desired width
        height (int): Desired height
        fps (int): Desired frames per second
        
    Returns:
        str: GStreamer pipeline string
    """
    if not GSTREAMER_AVAILABLE:
        logger.error("GStreamer not available, cannot create pipeline")
        return None
    
    # Check for Raspberry Pi camera module
    pi_camera_enabled = check_raspberry_pi_camera()
    
    # Try to find the appropriate source element
    available_sources = []
    
    # Check if libcamera-source is available
    pipeline_test = Gst.parse_launch("videotestsrc ! fakesink")
    if pipeline_test:
        factory = Gst.ElementFactory.find("libcamerasrc")
        if factory:
            available_sources.append("libcamerasrc")
        
        factory = Gst.ElementFactory.find("v4l2src")
        if factory:
            available_sources.append("v4l2src")
    
    logger.info(f"Available GStreamer source elements: {available_sources}")
    
    # Build pipeline based on available sources
    if "libcamerasrc" in available_sources and pi_camera_enabled:
        # Raspberry Pi with libcamera (modern approach)
        return (
            f"libcamerasrc ! video/x-raw, width={width}, height={height}, "
            f"framerate={fps}/1 ! videoconvert ! appsink"
        )
    elif "v4l2src" in available_sources:
        # Standard V4L2 source
        return (
            f"v4l2src device=/dev/video{camera_index} ! "
            f"video/x-raw, width={width}, height={height}, "
            f"framerate={fps}/1 ! videoconvert ! appsink"
        )
    else:
        # Fallback - let caller handle the failure
        logger.error("No suitable GStreamer source element found")
        return None

def create_camera_capture_gstreamer(camera_index=0, width=640, height=480, fps=30):
    """
    Create a camera capture using GStreamer with proper error handling.
    
    Args:
        camera_index (int): Camera index
        width (int): Desired width
        height (int): Desired height
        fps (int): Desired frames per second
        
    Returns:
        cv2.VideoCapture or None: Camera capture object if successful, None otherwise
    """
    if not GSTREAMER_AVAILABLE:
        logger.warning("GStreamer not available, falling back to standard camera capture")
        return create_camera_capture(camera_index, resolution=(width, height))
    
    pipeline_str = get_gstreamer_pipeline_str(camera_index, width, height, fps)
    if not pipeline_str:
        logger.warning("Failed to create GStreamer pipeline, falling back to standard capture")
        return create_camera_capture(camera_index, resolution=(width, height))
    
    logger.info(f"Attempting to create GStreamer camera with pipeline: {pipeline_str}")
    
    try:
        cap = cv2.VideoCapture(pipeline_str, cv2.CAP_GSTREAMER)
        if not cap.isOpened():
            logger.warning("Failed to open GStreamer camera, falling back to standard capture")
            return create_camera_capture(camera_index, resolution=(width, height))
        
        logger.info("Successfully created GStreamer camera capture")
        return cap
    except Exception as e:
        logger.error(f"Error creating GStreamer camera: {e}")
        logger.warning("Falling back to standard camera capture")
        return create_camera_capture(camera_index, resolution=(width, height))

# Demo function to test the camera utilities
def demo():
    """Run a demo of camera utilities"""
    logger.info("Starting camera utils demo...")
    
    # Check for available cameras
    cameras = get_available_cameras()
    logger.info(f"Found {len(cameras)} camera devices")
    
    # Try to find a working camera
    camera_index = find_working_camera()
    if camera_index is not None:
        logger.info(f"Found working camera at index {camera_index}")
        
        # Try to open it
        cap = create_camera_capture(camera_index)
        if cap is not None:
            logger.info("Camera capture created successfully")
            
            # Show video feed for 5 seconds
            try:
                start_time = time.time()
                while time.time() - start_time < 5:
                    ret, frame = cap.read()
                    if ret:
                        cv2.imshow("Camera Feed", frame)
                        if cv2.waitKey(1) & 0xFF == ord('q'):
                            break
                    else:
                        logger.warning("Failed to read frame")
                        break
            finally:
                cap.release()
                cv2.destroyAllWindows()
                logger.info("Camera demo completed")
        else:
            logger.error("Failed to create camera capture")
    else:
        logger.error("No working camera found")

if __name__ == "__main__":
    demo()
