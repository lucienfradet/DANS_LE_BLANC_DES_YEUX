#!/bin/bash

# Script to launch Dans le Blanc des Yeux art installation for Pi 5 with ribbon cameras
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

# Check PiCamera2
python3 -c "from picamera2 import Picamera2" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "Error: PiCamera2 module is required but not installed."
    echo "Please install with: pip3 install picamera2"
    exit 1
fi

# Install Python requirements
echo "Installing Python requirements..."
pip3 install -r requirements.txt

# Make sure picamera2 is installed
pip3 install picamera2

# Make sure keyboard module is installed
pip3 install keyboard

# Install keyboard with sudo for root-level key monitoring
if [ "$(id -u)" -ne 0 ]; then
    echo "Installing keyboard module with sudo for system-wide keyboard monitoring..."
    sudo pip3 install keyboard
fi

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
    echo "Checking Pi camera devices..."
    
    # Try to list camera info using libcamera-tools
    if command_exists libcamera-hello; then
        libcamera-hello --list-cameras
    elif command_exists v4l2-ctl; then
        v4l2-ctl --list-devices
    fi
    
    # Try to get PiCamera info using Python
    echo "Checking PiCamera with Python:"
    python3 -c "from picamera2 import Picamera2; print(f'Found {len(Picamera2.global_camera_info())} cameras'); print(Picamera2.global_camera_info())"
    
    # Update config.ini with appropriate camera information if needed
    if [ -f "config.ini" ]; then
        if ! grep -q "\[video\]" config.ini; then
            echo "Adding Pi camera settings to config.ini"
            # Use camera 0 for internal and camera 1 for external by default
            echo -e "\n[video]\ninternal_camera_id = 0\nexternal_camera_id = 1\nframe_width = 640\nframe_height = 480\njpeg_quality = 75" >> config.ini
        fi
    fi
    
    # Replace camera_manager.py with Pi 5 specific version
    echo "Installing Pi 5 specific camera manager..."
    # Backup original if it doesn't exist
    if [ ! -f camera_manager.py.orig ]; then
        cp camera_manager.py camera_manager.py.orig
    fi
    cp pi5_camera_manager.py camera_manager.py
    
    # Replace video_display.py with fixed version
    echo "Installing fixed video display..."
    # Backup original if it doesn't exist
    if [ ! -f video_display.py.orig ]; then
        cp video_display.py video_display.py.orig
    fi
    cp fixed_video_display.py video_display.py
fi

# If video is enabled, set up X11
if [ $DISABLE_VIDEO -eq 0 ]; then
    # Kill any existing X servers to avoid conflicts
    if [ "$(id -u)" -eq 0 ]; then
        pkill X || true
    else
        sudo pkill X || true
    fi
    
    # Start X server with sudo
    echo "Starting X server..."
    if [ "$(id -u)" -eq 0 ]; then
        X :0 -nocursor -keeptty -noreset -ac &
    else
        sudo X :0 -nocursor -keeptty -noreset -ac &
    fi
    X_PID=$!
    
    # Wait for X to initialize
    sleep 3
    
    # Set up environment
    export DISPLAY=:0
    
    # Allow current user to connect to X server
    CURRENT_USER=$(whoami)
    xhost +local:$CURRENT_USER
    
    # Disable screen blanking and power management
    xset s off
    xset -dpms
    xset s noblank
    
    # Try to start a minimal window manager (if available)
    # This helps ensure we get true fullscreen with no decorations
    if command_exists openbox; then
        openbox --config-file <(echo '<?xml version="1.0" encoding="UTF-8"?><openbox_config xmlns="http://openbox.org/3.4/rc"><applications><application class="*"><decor>no</decor><maximized>yes</maximized></application></applications></openbox_config>') &
        echo "Started openbox with no decorations"
    elif command_exists matchbox-window-manager; then
        matchbox-window-manager -use_titlebar no &
        echo "Started matchbox with no decorations"
    fi
fi

# Set OpenCV performance optimization variables
export OPENCV_VIDEOIO_PRIORITY_MSMF=0       # Disable Microsoft Media Foundation
export OPENCV_VIDEOIO_PRIORITY_INTEL_MFX=0  # Disable Intel Media SDK
export OPENCV_FFMPEG_LOGLEVEL=0             # Disable FFMPEG logging

# Update controller.py to use the correct parameters for CameraManager
if [ -f controller.py ]; then
    echo "Updating controller.py to use correct camera parameters..."
    
    # Backup controller.py if not already backed up
    if [ ! -f controller.py.orig ]; then
        cp controller.py controller.py.orig
    fi
    
    # Update CameraManager initialization to use external_camera_id instead of use_external_picam
    sed -i 's/camera_manager = CameraManager(.*)/camera_manager = CameraManager(\n                internal_camera_id=video_params.get("internal_camera_id", 0),\n                external_camera_id=video_params.get("external_camera_id", 1),\n                disable_missing=True\n            )/' controller.py
    
    # Update video parameter reading
    sed -i 's/video_params\["use_external_picam"\] = config.getboolean/video_params["external_camera_id"] = config.getint/g' controller.py
fi

# Start the application with appropriate arguments
echo "Starting Dans le Blanc des Yeux..."
echo "Note: Press Ctrl+V to toggle the visualizer on/off during runtime"

# Set up arguments
ARGS=""
if [ $ENABLE_VISUAL -eq 1 ]; then
    ARGS="$ARGS --visualize"
fi

if [ $DISABLE_VIDEO -eq 0 ]; then
    # Make sure X has proper time to initialize fully
    echo "Waiting for X server to fully initialize..."
    sleep 2
fi

if [ $DISABLE_VIDEO -eq 1 ]; then
    ARGS="$ARGS --disable-video"
fi

# Run the application with root privileges for keyboard access
if [ "$(id -u)" -ne 0 ]; then
    echo "Running controller with sudo for keyboard shortcut access..."
    sudo python3 controller.py $ARGS
else
    python3 controller.py $ARGS
fi

# Clean up X server if we started it
if [ -n "$X_PID" ]; then
    echo "Stopping X server (PID: $X_PID)..."
    kill $X_PID || true
fi

# If the application exits, show a message
echo "Application has exited."
