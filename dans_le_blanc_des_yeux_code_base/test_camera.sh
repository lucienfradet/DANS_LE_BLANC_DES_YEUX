#!/bin/bash

# Test script to capture frames from both cameras
# Usage: ./test_cameras.sh

# Create output directory
mkdir -p camera_test

echo "=== Testing Available Cameras ==="

# List all video devices
echo "Available video devices:"
ls -l /dev/video*

# Test v4l2 device capabilities
echo -e "\nDevice capabilities:"
for dev in /dev/video*; do
  echo "Testing $dev..."
  v4l2-ctl --device=$dev --info || echo "Failed to get info for $dev"
done

# Test USB camera (likely /dev/video0)
echo -e "\n=== Testing USB Camera (/dev/video0) ==="
if [ -e /dev/video0 ]; then
  # Show capabilities
  v4l2-ctl --device=/dev/video0 --list-formats-ext || echo "Failed to list formats"
  
  # Try to capture a frame using ffmpeg
  echo "Capturing frame with ffmpeg..."
  ffmpeg -f v4l2 -video_size 640x480 -i /dev/video0 -frames:v 1 camera_test/usb_camera.jpg 2>/dev/null || echo "Failed to capture with ffmpeg"
  
  # Alternative capture using v4l2-ctl
  echo "Capturing frame with v4l2-ctl..."
  v4l2-ctl --device=/dev/video0 --set-fmt-video=width=640,height=480,pixelformat=YUYV --stream-mmap --stream-count=1 --stream-to=camera_test/usb_camera_v4l2.jpg || echo "Failed to capture with v4l2-ctl"
else
  echo "/dev/video0 not found"
fi

# Test PiCamera (likely /dev/video20)
echo -e "\n=== Testing PiCamera (/dev/video20) ==="
if [ -e /dev/video20 ]; then
  # Show capabilities
  v4l2-ctl --device=/dev/video20 --list-formats-ext || echo "Failed to list formats"
  
  # Try to capture a frame using ffmpeg
  echo "Capturing frame with ffmpeg..."
  ffmpeg -f v4l2 -video_size 640x480 -i /dev/video20 -frames:v 1 camera_test/picamera.jpg 2>/dev/null || echo "Failed to capture with ffmpeg"
  
  # Alternative capture using v4l2-ctl
  echo "Capturing frame with v4l2-ctl..."
  v4l2-ctl --device=/dev/video20 --set-fmt-video=width=640,height=480,pixelformat=YUYV --stream-mmap --stream-count=1 --stream-to=camera_test/picamera_v4l2.jpg || echo "Failed to capture with v4l2-ctl"
else
  echo "/dev/video20 not found"
fi

# Try the libcamera tools if available
echo -e "\n=== Testing with libcamera tools ==="
if command -v libcamera-still &> /dev/null; then
  echo "Capturing frame with libcamera-still..."
  libcamera-still -t 500 -o camera_test/libcamera_still.jpg || echo "Failed to capture with libcamera-still"
fi

if command -v libcamera-vid &> /dev/null; then
  echo "Capturing frame with libcamera-vid..."
  libcamera-vid -t 500 --output camera_test/libcamera_vid.h264 || echo "Failed to capture with libcamera-vid"
fi

# Check results
echo -e "\n=== Results ==="
echo "Check the camera_test directory for captured images:"
ls -lh camera_test/

echo -e "\nTest complete! Look at the images to see which camera is working."
