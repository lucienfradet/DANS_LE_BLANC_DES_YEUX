#!/bin/bash

# Simple script to test Pi ribbon cameras for Dans le Blanc des Yeux
# Usage: ./run_cameras.sh

echo "=== Pi Camera Test ==="

# Make sure picamera2 is installed
if ! python3 -c "from picamera2 import Picamera2" &>/dev/null; then
  echo "Installing PiCamera2..."
  pip3 install picamera2
fi

# List available cameras
echo "Checking available cameras..."
python3 -c "from picamera2 import Picamera2; print(f'Found {len(Picamera2.global_camera_info())} cameras'); print(Picamera2.global_camera_info())"

# Update config.ini with camera settings
if [ -f "config.ini" ]; then
  if ! grep -q "\[video\]" config.ini; then
    echo "Adding camera settings to config.ini"
    echo -e "\n[video]\ninternal_camera_id = 0\nexternal_camera_id = 1\nframe_width = 640\nframe_height = 480\njpeg_quality = 75" >> config.ini
  fi
fi

# Run the camera test standalone
echo "Running camera test..."
python3 -c "from camera_manager import test_camera_manager; test_camera_manager()"

echo "Test complete."
