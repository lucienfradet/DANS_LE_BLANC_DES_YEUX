#!/usr/bin/env python3
"""
Test script to capture frames from multiple cameras using Python
This is helpful for testing both USB webcams and PiCamera
"""

import os
import cv2
import time
import subprocess
import numpy as np
from datetime import datetime

# Create output directory
os.makedirs("camera_test", exist_ok=True)

def test_opencv_cameras():
    """Test OpenCV-compatible cameras (USB webcams and some Pi cameras)"""
    print("\n=== Testing OpenCV-Compatible Cameras ===")
    
    # Try camera indices 0-10
    for camera_idx in range(10):
        print(f"\nTesting camera index {camera_idx}...")
        cap = cv2.VideoCapture(camera_idx)
        
        if not cap.isOpened():
            print(f"  Camera {camera_idx} not available")
            continue
        
        # Get camera info
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        
        print(f"  Camera {camera_idx} opened successfully")
        print(f"  Resolution: {width}x{height}, FPS: {fps}")
        
        # Capture a frame
        ret, frame = cap.read()
        if ret:
            # Save the frame
            filename = f"camera_test/opencv_camera_{camera_idx}.jpg"
            cv2.imwrite(filename, frame)
            print(f"  Frame captured and saved to {filename}")
            print(f"  Frame shape: {frame.shape}")
            
            # Calculate some stats to see if it's a valid image or just black/static
            avg_brightness = np.mean(frame)
            std_dev = np.std(frame)
            print(f"  Average brightness: {avg_brightness:.2f}")
            print(f"  Standard deviation: {std_dev:.2f}")
            
            if avg_brightness < 5 or std_dev < 5:
                print("  WARNING: Image appears to be very dark or uniform - may not be capturing correctly")
        else:
            print("  Failed to capture frame")
        
        # Release the camera
        cap.release()

def test_v4l2_devices():
    """Test V4L2 devices directly"""
    print("\n=== Testing V4L2 Devices ===")
    
    # Get list of video devices
    try:
        devices = [dev for dev in os.listdir('/dev') if dev.startswith('video')]
        devices.sort()
    except Exception as e:
        print(f"Error listing devices: {e}")
        return
    
    print(f"Found {len(devices)} video devices: {', '.join(devices)}")
    
    # Test each device
    for device in devices:
        device_path = f"/dev/{device}"
        print(f"\nTesting {device_path}...")
        
        # Try to get device info
        try:
            result = subprocess.run(['v4l2-ctl', '--device', device_path, '--info'],
                                    capture_output=True, text=True, check=False)
            if result.returncode == 0:
                print(f"  Device info: {result.stdout.splitlines()[0]}")
            else:
                print(f"  Failed to get device info: {result.stderr}")
                continue
        except Exception as e:
            print(f"  Error running v4l2-ctl: {e}")
            continue
        
        # Try to capture a frame
        try:
            filename = f"camera_test/v4l2_{device}.jpg"
            result = subprocess.run(['v4l2-ctl', '--device', device_path,
                                    '--set-fmt-video=width=640,height=480,pixelformat=MJPG',
                                    '--stream-mmap', '--stream-count=1',
                                    '--stream-to', filename],
                                    capture_output=True, text=True, check=False)
            
            if result.returncode == 0 and os.path.exists(filename) and os.path.getsize(filename) > 1000:
                print(f"  Frame captured successfully to {filename}")
            else:
                print(f"  Failed to capture frame: {result.stderr}")
                
                # Try with YUYV format instead
                filename = f"camera_test/v4l2_{device}_yuyv.jpg"
                result = subprocess.run(['v4l2-ctl', '--device', device_path,
                                        '--set-fmt-video=width=640,height=480,pixelformat=YUYV',
                                        '--stream-mmap', '--stream-count=1',
                                        '--stream-to', filename],
                                        capture_output=True, text=True, check=False)
                
                if result.returncode == 0 and os.path.exists(filename) and os.path.getsize(filename) > 1000:
                    print(f"  Frame captured successfully with YUYV format to {filename}")
                else:
                    print(f"  Failed to capture frame with YUYV format: {result.stderr}")
        except Exception as e:
            print(f"  Error capturing frame: {e}")

def test_picamera():
    """Test PiCamera using picamera2 module"""
    print("\n=== Testing PiCamera with picamera2 module ===")
    
    try:
        from picamera2 import Picamera2
        print("PiCamera2 module imported successfully")
        
        # Try each camera
        for camera_idx in range(2):  # Most Pi setups have 1-2 cameras
            try:
                print(f"\nTesting PiCamera {camera_idx}...")
                picam = Picamera2(camera_idx)
                
                # Configure camera
                config = picam.create_preview_configuration(
                    main={"size": (640, 480), "format": "RGB888"}
                )
                picam.configure(config)
                
                # Start camera
                picam.start()
                
                # Wait a moment for camera to initialize
                time.sleep(1)
                
                # Capture frame
                frame = picam.capture_array()
                
                if frame is not None:
                    # Get image stats
                    avg_brightness = np.mean(frame)
                    std_dev = np.std(frame)
                    
                    print(f"  Frame captured successfully")
                    print(f"  Frame shape: {frame.shape}")
                    print(f"  Average brightness: {avg_brightness:.2f}")
                    print(f"  Standard deviation: {std_dev:.2f}")
                    
                    # Save frame
                    filename = f"camera_test/picamera2_{camera_idx}.jpg"
                    # Convert to BGR for OpenCV
                    if len(frame.shape) == 3 and frame.shape[2] == 3:
                        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    else:
                        frame_bgr = frame
                    cv2.imwrite(filename, frame_bgr)
                    print(f"  Frame saved to {filename}")
                else:
                    print("  Failed to capture frame (returned None)")
                
                # Stop camera
                picam.stop()
                
            except Exception as e:
                print(f"  Error with PiCamera {camera_idx}: {e}")
    
    except ImportError:
        print("picamera2 module not available - skipping PiCamera test")
    except Exception as e:
        print(f"Error in PiCamera test: {e}")

if __name__ == "__main__":
    print(f"Camera Test Script - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("Testing all available cameras and saving frames to camera_test/ directory")
    
    # Run all tests
    test_opencv_cameras()
    test_v4l2_devices()
    test_picamera()
    
    print("\n=== Testing Complete ===")
    print("Check the camera_test directory for captured images")
