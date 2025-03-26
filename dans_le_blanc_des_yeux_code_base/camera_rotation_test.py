#!/usr/bin/env python3
"""
Test script for verifying camera rotations and aspect ratio preservation
for the Dans le Blanc des Yeux installation.

This script uses the CameraManager to access both cameras and displays
them with the specified rotations.
"""

import cv2
import numpy as np
import time
import sys
from camera_manager import CameraManager

def rotate_and_fit_frame(frame, rotation_type, window_width=1280, window_height=800):
    """
    Rotate frame and fit it to the display without stretching.
    
    Args:
        frame: The input frame to process
        rotation_type: Type of rotation ('90_clockwise', '180', etc.)
        window_width: Width of the display window
        window_height: Height of the display window
        
    Returns:
        Processed frame that fits the display without stretching
    """
    if frame is None:
        return None
        
    # Apply the appropriate rotation
    if rotation_type == '90_clockwise':
        rotated = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    elif rotation_type == '180':
        rotated = cv2.rotate(frame, cv2.ROTATE_180)
    elif rotation_type == '90_counterclockwise':
        rotated = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    else:
        rotated = frame.copy()
        
    # Create a black background image with the size of the display
    background = np.zeros((window_height, window_width, 3), dtype=np.uint8)
    
    # Calculate the aspect ratio of the rotated frame
    h, w = rotated.shape[:2]
    aspect_ratio = w / h
    
    # Calculate dimensions to maintain aspect ratio
    if (window_width / window_height) > aspect_ratio:
        # Window is wider than the frame's aspect ratio
        new_height = window_height
        new_width = int(new_height * aspect_ratio)
    else:
        # Window is taller than the frame's aspect ratio
        new_width = window_width
        new_height = int(new_width / aspect_ratio)
        
    # Resize the frame while maintaining aspect ratio
    resized = cv2.resize(rotated, (new_width, new_height))
    
    # Calculate position to center the frame on the background
    y_offset = (window_height - new_height) // 2
    x_offset = (window_width - new_width) // 2
    
    # Place the resized frame on the black background
    background[y_offset:y_offset+new_height, x_offset:x_offset+new_width] = resized
    
    return background

def test_camera_rotations():
    """
    Test camera rotations and aspect ratio preservation.
    Displays internal camera with 180° rotation and external camera with 90° clockwise rotation.
    """
    print("Starting camera rotation test...")
    
    # Initialize camera manager
    camera_manager = CameraManager()
    if not camera_manager.start():
        print("Failed to start camera manager")
        return
    
    # Wait for cameras to initialize
    print("Waiting for cameras to initialize...")
    time.sleep(2)
    
    # Create display windows
    cv2.namedWindow("Internal Camera (180°)", cv2.WINDOW_NORMAL)
    cv2.namedWindow("External Camera (90° CW)", cv2.WINDOW_NORMAL)
    
    # Set window sizes (for testing, not fullscreen)
    cv2.resizeWindow("Internal Camera (180°)", 640, 480)
    cv2.resizeWindow("External Camera (90° CW)", 640, 480)
    
    try:
        while True:
            # Get frames from both cameras
            internal_frame = camera_manager.get_internal_frame()
            external_frame = camera_manager.get_external_frame()
            
            if internal_frame is not None:
                # Add orientation markers to help verify rotation
                h, w = internal_frame.shape[:2]
                # Draw arrows pointing up
                cv2.arrowedLine(internal_frame, (w//2, h//2), (w//2, h//4), (0, 255, 0), 3)
                cv2.putText(internal_frame, "UP", (w//2 - 20, h//4 - 10), 
                          cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                
                # Rotate internal camera 180 degrees and preserve aspect ratio
                rotated_internal = rotate_and_fit_frame(internal_frame, '180', 640, 480)
                cv2.imshow("Internal Camera (180°)", rotated_internal)
            
            if external_frame is not None:
                # Add orientation markers to help verify rotation
                h, w = external_frame.shape[:2]
                # Draw arrows pointing up
                cv2.arrowedLine(external_frame, (w//2, h//2), (w//2, h//4), (0, 255, 0), 3)
                cv2.putText(external_frame, "UP", (w//2 - 20, h//4 - 10), 
                          cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                
                # Rotate external camera 90 degrees clockwise and preserve aspect ratio
                rotated_external = rotate_and_fit_frame(external_frame, '90_clockwise', 640, 480)
                cv2.imshow("External Camera (90° CW)", rotated_external)
            
            # Check for key press
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('1'):
                print("Switching internal rotation to 180°")
            elif key == ord('2'):
                print("Switching internal rotation to 90° CW")
            elif key == ord('3'):
                print("Switching internal rotation to 90° CCW")
            elif key == ord('4'):
                print("Switching external rotation to 180°")
            elif key == ord('5'):
                print("Switching external rotation to 90° CW")
            elif key == ord('6'):
                print("Switching external rotation to 90° CCW")
            
            # Brief pause to control frame rate
            time.sleep(0.033)  # ~30fps
            
    except KeyboardInterrupt:
        print("Test interrupted by user")
    except Exception as e:
        print(f"Error during test: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Clean up
        camera_manager.stop()
        cv2.destroyAllWindows()
        print("Camera test completed")

if __name__ == "__main__":
    print("Camera Rotation Test")
    print("--------------------")
    print("Controls:")
    print("  q: Quit")
    print("  1-3: Change internal camera rotation")
    print("  4-6: Change external camera rotation")
    
    test_camera_rotations()
