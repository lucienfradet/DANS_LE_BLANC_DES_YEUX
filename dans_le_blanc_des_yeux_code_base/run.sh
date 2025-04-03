#!/bin/bash

# Script to launch Dans le Blanc des Yeux art installation for Pi 5 with ribbon cameras
# Usage: ./run.sh [visual] [disable-video] [disable-audio]
#   visual - Enable terminal visualization (optional)
#   disable-video - Disable video components (optional)
#   disable-audio - Disable audio components (optional)

# Parse arguments
ENABLE_VISUAL=0
DISABLE_VIDEO=0
DISABLE_AUDIO=0
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
    if [ "$arg" == "disable-audio" ]; then
        DISABLE_AUDIO=1
        echo "Audio components disabled"
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
    echo "Please install with: sudo apt install python3-opencv"
    exit 1
fi

# Check PiCamera2
python3 -c "from picamera2 import Picamera2" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "Error: PiCamera2 module is required but not installed."
    echo "Please install with: sudo apt install python3-picamera2"
    exit 1
fi

# Install Python requirements using apt where possible
echo "Installing Python requirements..."
sudo apt install -y python3-pip python3-numpy python3-serial python3-opencv python3-pyaudio

# For packages not available via apt, use pip with requirements file
pip3 install -r requirements.txt 2>/dev/null || echo "Some pip packages may not have installed. This is okay if they're available via apt."

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

# Check JACK dependencies
echo "Checking JACK dependencies..."
if ! command -v jackd >/dev/null 2>&1; then
    echo "Installing JACK audio dependencies..."
    sudo apt install -y jackd2 libjack-jackd2-dev
fi

if ! command -v jack.udp_sender >/dev/null 2>&1; then
    echo "Installing zita-njbridge for NetJack functionality..."
    sudo apt install -y zita-njbridge
fi

# Install Python JACK client
echo "Installing Python JACK client..."
pip3 install JACK-client

# Set up audio for JACK if audio is enabled
if [ $DISABLE_AUDIO -eq 0 ]; then
    echo "Setting up audio for JACK..."
    
    # Kill any existing JACK processes
    killall -9 jackd 2>/dev/null || true
    killall -9 jack.udp_sender 2>/dev/null || true
    killall -9 jack.udp_receiver 2>/dev/null || true
    
    # Unmute all audio devices (still useful for initial setup)
    if command_exists amixer; then
        # Try to unmute master volume
        amixer sset Master unmute >/dev/null 2>&1 || true
        amixer sset Master 80% >/dev/null 2>&1 || true
        
        # Try to unmute any capture devices
        amixer sset Capture unmute >/dev/null 2>&1 || true
        amixer sset Capture 80% >/dev/null 2>&1 || true
        
        # Try to set TX 96Khz audio device unmuted if available
        amixer -c 3 sset 'Speaker' unmute >/dev/null 2>&1 || true
        amixer -c 3 sset 'Speaker' 80% >/dev/null 2>&1 || true
        amixer -c 3 sset 'Mic' unmute >/dev/null 2>&1 || true
        amixer -c 3 sset 'Mic' 80% >/dev/null 2>&1 || true
    fi
    
    # Check available audio devices
    echo "Available audio devices:"
    arecord -l
    aplay -l
    
    # JACK will be started by the AudioSystem class
    echo "JACK will be initialized by the audio_system module"
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
fi

# If video is enabled, set up X11 for Waveshare 7-inch display
if [ $DISABLE_VIDEO -eq 0 ]; then
    echo "Setting up X server for Waveshare 7-inch display (1280x800)..."
    
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
    echo "Waiting for X server to initialize..."
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
    
    # Set environment variables for Waveshare display dimensions
    export WAVESHARE_WIDTH=1024
    export WAVESHARE_HEIGHT=600
    
    # Try to start a minimal window manager (if available)
    # This helps ensure we get true fullscreen with no decorations
    if command_exists openbox; then
        echo "Starting openbox with no decorations..."
        openbox --config-file <(echo '<?xml version="1.0" encoding="UTF-8"?><openbox_config xmlns="http://openbox.org/3.4/rc"><applications><application class="*"><decor>no</decor><maximized>yes</maximized></application></applications></openbox_config>') &
    elif command_exists matchbox-window-manager; then
        echo "Starting matchbox with no decorations..."
        matchbox-window-manager -use_titlebar no &
    fi
    
    # Configure rotation of display if needed (uncomment if required)
    # xrandr --output HDMI-1 --rotate normal
    
    echo "X server is ready for display"

    if [ $DISABLE_VIDEO -eq 0 ]; then
      # Echo screen dimensions using xrandr
      echo "Current screen dimensions:"
      xrandr | grep -w connected | grep -o '[0-9]*x[0-9]*'

      echo "Environment variables for screen dimensions:"
      echo "Width: $WAVESHARE_WIDTH, Height: $WAVESHARE_HEIGHT"
    fi
fi

# Set up audio settings if audio is enabled
if [ $DISABLE_AUDIO -eq 0 ]; then
    echo "Setting up audio devices..."
    
    # Unmute all audio devices
    if command_exists amixer; then
        # Try to unmute master volume
        amixer sset Master unmute >/dev/null 2>&1 || true
        amixer sset Master 80% >/dev/null 2>&1 || true
        
        # Try to unmute any capture devices
        amixer sset Capture unmute >/dev/null 2>&1 || true
        amixer sset Capture 80% >/dev/null 2>&1 || true
        
        # Try to set TX 96Khz audio device unmuted if available
        amixer -c 3 sset 'Speaker' unmute >/dev/null 2>&1 || true
        amixer -c 3 sset 'Speaker' 80% >/dev/null 2>&1 || true
        amixer -c 3 sset 'Mic' unmute >/dev/null 2>&1 || true
        amixer -c 3 sset 'Mic' 80% >/dev/null 2>&1 || true
    fi
    
    # Set PulseAudio volume levels if PulseAudio is running
    if command_exists pactl; then
        # Try to set default sink volume
        pactl set-sink-volume @DEFAULT_SINK@ 80% >/dev/null 2>&1 || true
        pactl set-sink-mute @DEFAULT_SINK@ 0 >/dev/null 2>&1 || true
        
        # Try to set default source volume
        pactl set-source-volume @DEFAULT_SOURCE@ 80% >/dev/null 2>&1 || true
        pactl set-source-mute @DEFAULT_SOURCE@ 0 >/dev/null 2>&1 || true
    fi
fi

# Set OpenCV performance optimization variables
export OPENCV_VIDEOIO_PRIORITY_MSMF=0       # Disable Microsoft Media Foundation
export OPENCV_VIDEOIO_PRIORITY_INTEL_MFX=0  # Disable Intel Media SDK
export OPENCV_FFMPEG_LOGLEVEL=0             # Disable FFMPEG logging

# Start the application with appropriate arguments
echo "Starting Dans le Blanc des Yeux..."
echo "Console commands available:"
echo "- Type 'v' and press Enter to toggle the visualizer on/off"
echo "- Type 'q' and press Enter to quit the application"
echo "- Press Ctrl+C to stop the application"

# Set up arguments
ARGS=""
if [ $ENABLE_VISUAL -eq 1 ]; then
    ARGS="$ARGS --visualize"
fi

if [ $DISABLE_VIDEO -eq 1 ]; then
    ARGS="$ARGS --disable-video"
fi

if [ $DISABLE_AUDIO -eq 1 ]; then
    ARGS="$ARGS --disable-audio"
fi

if [ $DISABLE_VIDEO -eq 0 ]; then
    # Make sure X has proper time to initialize fully
    echo "Waiting for X server to fully initialize..."
    sleep 2
fi

# Run the application
python3 controller.py $ARGS

# Clean up X server if we started it
if [ -n "$X_PID" ]; then
    echo "Stopping X server (PID: $X_PID)..."
    kill $X_PID || true
fi

# If the application exits, show a message
echo "Application has exited."
