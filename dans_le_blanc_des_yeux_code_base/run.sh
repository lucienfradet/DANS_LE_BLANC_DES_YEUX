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
    
    # Update config.ini with appropriate camera information
    # For Pi 5, we should use correct camera ID from detected devices
    if [ -f "config.ini" ]; then
        # Try to detect proper camera settings
        # For PiCamera: Usually /dev/video0-7 on Pi 5 (rp1-cfe)
        if grep -q "internal_camera_id" config.ini; then
            echo "Using existing camera settings in config.ini"
        else
            echo "Adding default camera settings to config.ini (PiCamera for Pi 5)"
            # We'll use /dev/video0 as a starting point for Pi 5
            echo -e "\n[video]\ninternal_camera_id = 0\nuse_external_picam = True\nframe_width = 640\nframe_height = 480\njpeg_quality = 75\ndefault_layout = grid" >> config.ini
        fi
    fi
fi

# Set up display for video (if not disabled)
if [ $DISABLE_VIDEO -eq 0 ]; then
    # Check if X server is already running
    if ! DISPLAY=:0 xset q &>/dev/null; then
        echo "Starting X server with sudo..."
        
        # Kill any existing X servers to avoid conflicts
        sudo pkill X || true
        
        # Check if we need to add the user to input/video groups
        CURRENT_USER=$(whoami)
        if ! groups $CURRENT_USER | grep -q "input"; then
            echo "Adding user to input group for X server permissions"
            sudo usermod -a -G input $CURRENT_USER
        fi
        
        # Try to start X server with sudo
        sudo X :0 -nocursor -keeptty -noreset &
        X_PID=$!
        
        # Wait for X to initialize
        sleep 3
        
        if DISPLAY=:0 xset q &>/dev/null; then
            echo "✅ X server started successfully with PID: $X_PID"
            
            # Export display environment variable
            export DISPLAY=:0
            
            # Fix permissions for the X server
            xhost +local:$CURRENT_USER
            
            # Disable screen blanking and power management
            xset s off
            xset -dpms
            xset s noblank
        else
            echo "❌ Failed to start X server."
            echo "You may need to run 'sudo raspi-config' and enable 'Boot to Desktop' or 'Console Autologin'"
            echo "For now, continuing without display capabilities."
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

# Run with the assembled arguments
if [ -n "$ARGS" ]; then
    python3 controller.py $ARGS
else
    python3 controller.py
fi

# Clean up X server if we started it
if [ -n "$X_PID" ]; then
    echo "Stopping X server (PID: $X_PID)..."
    sudo kill $X_PID || true
fi

# If the application exits, show a message
echo "Application has exited."
