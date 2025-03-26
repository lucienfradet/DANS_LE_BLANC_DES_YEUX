#!/usr/bin/env python3
"""
Direct camera viewer for testing camera devices
Usage: python3 view_camera.py [device_path]
Examples:
  python3 view_camera.py            # Use OpenCV with camera index 0
  python3 view_camera.py 1          # Use OpenCV with camera index 1
  python3 view_camera.py /dev/video0  # Use V4L2 directly
  python3 view_camera.py /dev/video20 # Use V4L2 directly
  python3 view_camera.py picam      # Use PiCamera2
"""

import sys
import cv2
import os
import numpy as np
import time

def view_opencv_camera(camera_idx):
    """View camera using OpenCV"""
    try:
        camera_idx = int(camera_idx)
    except ValueError:
        camera_idx = 0
        
    print(f"Opening OpenCV camera with index {camera_idx}")
    cap = cv2.VideoCapture(camera_idx)
    
    if not cap.isOpened():
        print(f"Failed to open camera with index {camera_idx}")
        return False
    
    # Get camera properties
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    
    print(f"Camera opened: {width}x{height} @ {fps}fps")
    
    cv2.namedWindow("Camera View", cv2.WINDOW_NORMAL)
    
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Failed to capture frame")
                break
                
            # Add info text
            cv2.putText(frame, f"OpenCV Camera {camera_idx}", (10, 30), 
                      cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            cv2.putText(frame, f"{width}x{height} @ {fps}fps", (10, 70), 
                      cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            
            # Show frame
            cv2.imshow("Camera View", frame)
            
            # Check for key press
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q') or key == 27:  # q or ESC
                break
            elif key == ord('s'):  # s to save
                cv2.imwrite(f"camera_{camera_idx}_{time.time()}.jpg", frame)
                print(f"Saved frame to camera_{camera_idx}_{time.time()}.jpg")
    
    finally:
        cap.release()
        cv2.destroyAllWindows()
        
    return True

def view_v4l2_camera(device_path):
    """View camera using V4L2 directly"""
    if not os.path.exists(device_path):
        print(f"Device {device_path} does not exist")
        return False
        
    print(f"Opening V4L2 device {device_path}")
    
    try:
        # Open device with V4L2 backend
        cap = cv2.VideoCapture(device_path, cv2.CAP_V4L2)
        
        if not cap.isOpened():
            print(f"Failed to open device {device_path}")
            return False
        
        # Try to set properties
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        
        # Get actual properties
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        
        print(f"Device opened: {width}x{height} @ {fps}fps")
        
        cv2.namedWindow("Camera View", cv2.WINDOW_NORMAL)
        
        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    print("Failed to capture frame")
                    break
                    
                # Add info text
                cv2.putText(frame, f"V4L2 Device: {os.path.basename(device_path)}", (10, 30), 
                          cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                cv2.putText(frame, f"{width}x{height} @ {fps}fps", (10, 70), 
                          cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
                
                # Show frame
                cv2.imshow("Camera View", frame)
                
                # Check for key press
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q') or key == 27:  # q or ESC
                    break
                elif key == ord('s'):  # s to save
                    cv2.imwrite(f"camera_v4l2_{time.time()}.jpg", frame)
                    print(f"Saved frame to camera_v4l2_{time.time()}.jpg")
        
        finally:
            cap.release()
            cv2.destroyAllWindows()
            
        return True
        
    except Exception as e:
        print(f"Error viewing V4L2 device: {e}")
        return False

def view_picamera():
    """View camera using PiCamera2 module"""
    try:
        from picamera2 import Picamera2
        from picamera2.previews import QtPreview
        import time
        
        print("Initializing PiCamera2...")
        
        # Try to initialize PiCamera
        try:
            picam = Picamera2()
            
            # Configure camera
            config = picam.create_preview_configuration()
            picam.configure(config)
            
            # Create GUI preview
            preview = QtPreview(picam)
            
            # Start camera
            picam.start()
            preview.start()
            
            print("PiCamera preview started")
            print("Press Ctrl+C to exit")
            
            try:
                # Keep running until Ctrl+C
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\nStopping PiCamera...")
            finally:
                # Clean up
                picam.stop()
                preview.stop()
                
            return True
            
        except Exception as e:
            print(f"Error initializing PiCamera: {e}")
            return False
            
    except ImportError:
        print("PiCamera2 module not available")
        print("Install with: pip install picamera2")
        return False

def main():
    # Get device argument if provided
    if len(sys.argv) > 1:
        device = sys.argv[1]
        
        # Check if it's a numeric index
        try:
            camera_idx = int(device)
            view_opencv_camera(camera_idx)
        except ValueError:
            # Check if it's a device path
            if device.startswith('/dev/video'):
                view_v4l2_camera(device)
            elif device.lower() == 'picam':
                view_picamera()
            else:
                print(f"Unknown device: {device}")
                print("Usage: python3 view_camera.py [device_path]")
    else:
        # Default to OpenCV camera 0
        view_opencv_camera(0)

if __name__ == "__main__":
    main()
