#!/bin/bash

# Script to launch Dans le Blanc des Yeux art installation
# Usage: ./run.sh [visual] [disable-video] [headless]
#   visual - Enable terminal visualization (optional)
#   disable-video - Disable video components (optional)
#   headless - Use headless display mode without X11/Qt (optional)

# Parse arguments
ENABLE_VISUAL=0
DISABLE_VIDEO=0
HEADLESS_MODE=0
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
    if [ "$arg" == "headless" ]; then
        HEADLESS_MODE=1
        echo "Headless display mode enabled"
    fi
done

# Ensure we're in the right directory
cd "$(dirname "$0")"

# Check dependencies
echo "Checking dependencies..."

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

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
    
    # Check for PiCamera
    if command_exists vcgencmd; then
        echo "Checking PiCamera status..."
        vcgencmd get_camera
    fi
fi

# Set up display for video (if not disabled)
if [ $DISABLE_VIDEO -eq 0 ] && [ $HEADLESS_MODE -eq 0 ]; then
    # Check if X server is already running
    if ! DISPLAY=:0 xset q &>/dev/null; then
        echo "Starting minimal X server..."
        
        # Kill any existing X servers to avoid conflicts
        pkill X || true
        
        # Start a minimal X server with no cursor
        X :0 -nocursor &
        X_PID=$!
        
        # Wait for X to initialize
        sleep 2
        
        if DISPLAY=:0 xset q &>/dev/null; then
            echo "✅ X server started successfully with PID: $X_PID"
            
            # Export environment variables
            export DISPLAY=:0
            export XAUTHORITY=~/.Xauthority
            
            # Disable screen blanking and power management
            xset s off
            xset -dpms
            xset s noblank
        else
            echo "❌ Failed to start X server. Falling back to headless mode."
            HEADLESS_MODE=1
        fi
    else
        echo "✅ X server already running"
        export DISPLAY=:0
        
        # Disable screen blanking and power management
        xset s off
        xset -dpms
        xset s noblank
    fi
fi

# Set OpenCV performance optimization variables
export OPENCV_VIDEOIO_PRIORITY_MSMF=0       # Disable Microsoft Media Foundation
export OPENCV_VIDEOIO_PRIORITY_INTEL_MFX=0  # Disable Intel Media SDK
export OPENCV_FFMPEG_LOGLEVEL=0             # Disable FFMPEG logging

# Start the application with appropriate arguments
echo "Starting Dans le Blanc des Yeux..."
ARGS=""

if [ $ENABLE_VISUAL -eq 1 ]; then
    ARGS="$ARGS --visualize"
fi

if [ $DISABLE_VIDEO -eq 1 ]; then
    ARGS="$ARGS --disable-video"
fi

if [ $HEADLESS_MODE -eq 1 ]; then
    ARGS="$ARGS --headless"
fi

# Run with the assembled arguments
if [ -n "$ARGS" ]; then
    python3 controller.py $ARGS
else
    python3 controller.py
fi

# Clean up X server if we started it
if [ -n "$X_PID" ]; then
    echo "Stopping X server (PID: $X_PID)..."
    kill $X_PID || true
fi

# If the application exits, show a message
echo "Application has exited."
