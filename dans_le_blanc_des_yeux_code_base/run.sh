#!/bin/bash

# Script to launch Dans le Blanc des Yeux art installation for Pi 5 with ribbon cameras
# Usage: ./run.sh [visual] [disable-video] [disable-audio] [service]
#   visual - Enable terminal visualization (optional)
#   disable-video - Disable video components (optional)
#   disable-audio - Disable audio components (optional)
#   service - Run in service mode without input monitor (optional)

# Parse arguments
ENABLE_VISUAL=0
DISABLE_VIDEO=0
DISABLE_AUDIO=0
SERVICE_MODE=0
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
    if [ "$arg" == "service" ]; then
        SERVICE_MODE=1
        echo "Running in service mode (no input monitor)"
    fi
done

# Check if already running using a lock file
LOCK_FILE="/tmp/dans_le_blanc.lock"

if [ -f "$LOCK_FILE" ]; then
    # Check if process is still running
    PID=$(cat "$LOCK_FILE")
    if ps -p $PID > /dev/null; then
        echo "ERROR: Application already running with PID $PID"
        echo "Use 'kill $PID' to stop it first, or remove $LOCK_FILE if the process died unexpectedly"
        exit 1
    else
        echo "Removing stale lock file"
        rm -f "$LOCK_FILE"
    fi
fi

# Create lock file with current PID
echo $$ > "$LOCK_FILE"

# Ensure cleanup on exit
trap "rm -f $LOCK_FILE" EXIT

# Kill any leftover processes from previous runs
echo "Cleaning up any leftover processes..."
pkill -f "python3 controller.py" || true
sudo pkill X || true
killall pulseaudio || true
sleep 2

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

# Install GStreamer and all required components
install_gstreamer() {
    echo "Installing GStreamer and all required components..."
    
    # Update package lists
    sudo apt update
    
    # Install GStreamer core components
    sudo apt install -y \
        gstreamer1.0-tools \
        gstreamer1.0-plugins-base \
        gstreamer1.0-plugins-good \
        gstreamer1.0-plugins-bad \
        gstreamer1.0-plugins-ugly \
        gstreamer1.0-alsa \
        gstreamer1.0-gl
    
    # Install GStreamer development packages
    sudo apt install -y \
        libgstreamer1.0-dev \
        libgstreamer-plugins-base1.0-dev \
        libgstreamer-plugins-good1.0-dev \
        libgstreamer-plugins-bad1.0-dev
    
    # Install Python GStreamer bindings
    sudo apt install -y \
        python3-gi \
        python3-gi-cairo \
        python3-gst-1.0 \
        gir1.2-gstreamer-1.0 \
        gir1.2-gst-plugins-base-1.0 \
        gir1.2-gst-plugins-bad-1.0
    
    echo "GStreamer installation completed."
}

# Check basic GStreamer
python3 -c "import gi; gi.require_version('Gst', '1.0'); from gi.repository import Gst" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "GStreamer Python bindings not found. Installing GStreamer components..."
    install_gstreamer
fi

# Check GStreamer Audio specifically
python3 -c "import gi; gi.require_version('Gst', '1.0'); gi.require_version('GstAudio', '1.0'); from gi.repository import Gst, GstAudio" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "GStreamer Audio module missing. Installing additional GStreamer components..."
    install_gstreamer
    
    # Verify installation after installing
    python3 -c "import gi; gi.require_version('Gst', '1.0'); gi.require_version('GstAudio', '1.0'); from gi.repository import Gst, GstAudio; Gst.init(None); print('GStreamer', Gst.version_string())" 2>/dev/null
    if [ $? -ne 0 ]; then
        echo "WARNING: GStreamer Audio installation failed. Audio functionality will be limited."
    else
        echo "GStreamer Audio module installed successfully."
    fi
else
    echo "GStreamer Audio module is already installed."
fi

# Install Python requirements using apt where possible
echo "Installing Python requirements..."
sudo apt install -y python3-pip python3-numpy python3-serial python3-opencv

# For packages not available via apt, use pip with requirements file
pip3 install -r requirements.txt 2>/dev/null || echo "Some pip packages may not have installed. This is okay if they're available via apt."

# Fix Openbox menu error
if command_exists openbox; then
    echo "Creating default Openbox menu to avoid error messages..."
    sudo mkdir -p /var/lib/openbox
    if [ ! -f "/var/lib/openbox/debian-menu.xml" ]; then
        echo '<?xml version="1.0" encoding="UTF-8"?>
<openbox_menu>
<menu id="root-menu" label="Openbox 3">
  <item label="Terminal"><action name="Execute"><command>x-terminal-emulator</command></action></item>
  <item label="Reconfigure"><action name="Reconfigure"/></item>
  <item label="Exit"><action name="Exit"/></item>
</menu>
</openbox_menu>' | sudo tee /var/lib/openbox/debian-menu.xml > /dev/null
    fi
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

# Check audio devices if audio is enabled
if [ $DISABLE_AUDIO -eq 0 ]; then
    echo "Setting up audio devices..."
    
    # Make sure ALSA and PulseAudio are properly installed
    sudo apt install -y alsa-utils pulseaudio
    
    # Restart ALSA to ensure audio devices are properly recognized
    echo "Restarting ALSA sound system..."
    sudo alsa force-reload || true
    sleep 1

    # Make sure PulseAudio is running
    pulseaudio --check || { pulseaudio --start; echo "Started PulseAudio"; }

    # Wait for PulseAudio to be fully ready with device detection
    echo "Waiting for PulseAudio to initialize audio devices..."
    MAX_WAIT=10
    for i in $(seq 1 $MAX_WAIT); do
        # Check if we can get a device list with actual devices
        DEVICE_COUNT=$(pactl list sources short 2>/dev/null | grep -v "auto_null" | wc -l)
        
        if [ "$DEVICE_COUNT" -gt 0 ]; then
            echo "PulseAudio ready after $i seconds with $DEVICE_COUNT devices detected"
            break
        fi
        
        echo "Waiting for PulseAudio devices... ($i/$MAX_WAIT)"
        sleep 1
        
        # On the 5th second, try to reload modules to speed up detection
        if [ "$i" -eq 5 ]; then
            echo "Attempting to refresh PulseAudio device list..."
            pactl load-module module-detect 2>/dev/null || true
        fi
    done
    
    # List available audio devices using GStreamer
    echo "GStreamer audio devices:"
    gst-device-monitor-1.0 Audio/Source || true
    echo "GStreamer audio output devices:"
    gst-device-monitor-1.0 Audio/Sink || true
    
    # Also list ALSA devices 
    echo "ALSA input devices:"
    arecord -l || true
    echo "ALSA output devices:"
    aplay -l || true
    
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
    
    echo "Audio setup complete"
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

# Set OpenCV performance optimization variables
export OPENCV_VIDEOIO_PRIORITY_MSMF=0       # Disable Microsoft Media Foundation
export OPENCV_VIDEOIO_PRIORITY_INTEL_MFX=0  # Disable Intel Media SDK
export OPENCV_FFMPEG_LOGLEVEL=0             # Disable FFMPEG logging

# Set GStreamer debug level (uncomment to enable debugging)
# export GST_DEBUG=3
# export GST_DEBUG_FILE=gstreamer_debug.log

# Initialize GStreamer in Python before launching the application
python3 -c "import gi; gi.require_version('Gst', '1.0'); from gi.repository import Gst; Gst.init(None)" 2>/dev/null || echo "Warning: Failed to initialize GStreamer"

# Start the application with appropriate arguments
echo "Starting Dans le Blanc des Yeux..."

# Only show commands if not in service mode
if [ $SERVICE_MODE -eq 0 ]; then
    echo "Console commands available:"
    echo "- Type 'v' and press Enter to toggle the visualizer on/off"
    echo "- Type 'q' and press Enter to quit the application"
    echo "- Press Ctrl+C to stop the application"
else
    echo "Running in service mode (no console commands available)"
fi

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

if [ $SERVICE_MODE -eq 1 ]; then
    ARGS="$ARGS --service"
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
