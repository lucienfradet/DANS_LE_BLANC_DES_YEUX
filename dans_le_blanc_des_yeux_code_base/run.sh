#!/bin/bash

# Script to launch Dans le Blanc des Yeux art installation
# Usage: ./run.sh [visual] [disable-video]
#   visual - Enable terminal visualization (optional)
#   disable-video - Disable video components (optional)

# Parse arguments
ENABLE_VISUAL=0
DISABLE_VIDEO=0
for arg in "$@"
do
    if [ "$arg" == "visual" ]; then
        ENABLE_VISUAL=1
        echo "Terminal visualization enabled"
    fi
    if [ "$arg" == "disable-video" ]; then
        DISABLE_VIDEO=1
        echo "Video components disabled"
    fi
done

# Export display variable for X11 forwarding if needed
# Point to the physical display of the Raspberry Pi
export DISPLAY=:0

# Ensure we're in the right directory
cd "$(dirname "$0")"

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check dependencies
echo "Checking dependencies..."

# Check Python
if ! command_exists python3; then
    echo "Error: Python 3 is required but not installed."
    exit 1
fi

# Check OpenCV
python3 -c "import cv2" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "Error: OpenCV for Python is required but not installed."
    echo "Please install with: pip3 install opencv-python"
    exit 1
fi

# Check if PiCamera2 is available when video is enabled
if [ $DISABLE_VIDEO -eq 0 ]; then
    python3 -c "from picamera2 import Picamera2" 2>/dev/null
    if [ $? -ne 0 ]; then
        echo "Warning: PiCamera2 module not found. External camera might not work."
        echo "You can install it with: pip3 install picamera2"
        # Continue anyway, internal camera might still work
    fi
fi

# Install Python requirements
echo "Installing Python requirements..."
pip3 install -r requirements.txt

# Check if Arduino is connected
if [ ! -e "/dev/ttyACM0" ]; then
    echo "Warning: Arduino device not found at /dev/ttyACM0"
    echo "Please check the connection or update the port in serial_handler.py"
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Check for cameras if video is enabled
if [ $DISABLE_VIDEO -eq 0 ]; then
    echo "Checking camera devices..."
    if command_exists v4l2-ctl; then
        v4l2-ctl --list-devices
    else
        ls -l /dev/video*
    fi
fi

# Set up the display before starting
if [ $DISABLE_VIDEO -eq 0 ]; then
    # Try to turn off screensaver and power saving
    if command_exists xset; then
        echo "Disabling screen blanking and power management..."
        xset s off
        xset -dpms
        xset s noblank
    fi
fi

# Start the application with appropriate arguments
echo "Starting Dans le Blanc des Yeux..."
ARGS=""

if [ $ENABLE_VISUAL -eq 1 ]; then
    ARGS="$ARGS --visualize"
fi

if [ $DISABLE_VIDEO -eq 1 ]; then
    ARGS="$ARGS --disable-video"
fi

# Run with the assembled arguments
if [ -n "$ARGS" ]; then
    python3 controller.py $ARGS
else
    python3 controller.py
fi

# If the application exits, show a message
echo "Application has exited."
