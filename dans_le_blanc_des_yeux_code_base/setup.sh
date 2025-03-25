#!/bin/bash

# Setup script for Dans le Blanc des Yeux art installation

echo "=== Dans le Blanc des Yeux - Setup ==="
echo "This script will set up the necessary dependencies and configurations."

# Check if running as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (use sudo)"
  exit 1
fi

# Update package lists
echo "Updating package lists..."
apt-get update

# Install required packages
echo "Installing required packages..."
apt-get install -y \
  python3-pip \
  python3-opencv \
  python3-gi \
  python3-gst-1.0 \
  gstreamer1.0-tools \
  gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly \
  gstreamer1.0-libav \
  libgirepository1.0-dev \
  git \
  arduino-cli

# Create a Python virtual environment (optional)
echo "Creating Python virtual environment..."
su - $SUDO_USER -c "python3 -m venv ~/dans_le_blanc_des_yeux_env --system-site-packages"

# Install Python packages
echo "Installing Python packages..."
su - $SUDO_USER -c "~/dans_le_blanc_env/bin/pip install -r $(pwd)/requirements.txt"
su - $SUDO_USER -c "~/dans_le_blanc_env/bin/pip install opencv-python pythonosc pyserial numpy"

# Configure auto-start
echo "Setting up auto-start on boot..."
cat > /etc/systemd/system/dans-le-blanc-des-yeux.service << EOF
[Unit]
Description=Dans le Blanc des Yeux Art Installation
After=network.target

[Service]
User=$SUDO_USER
WorkingDirectory=$(pwd)
ExecStart=/bin/bash $(pwd)/run.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Enable the service
systemctl enable dans-le-blanc-des-yeux.service

# Make run.sh executable
chmod +x run.sh

# Configure Arduino permissions
echo "Configuring Arduino permissions..."
usermod -a -G dialout $SUDO_USER

# Set up Tailscale for networking (if not already set up)
if ! command -v tailscale &> /dev/null; then
    echo "Installing Tailscale for networking..."
    curl -fsSL https://tailscale.com/install.sh | sh
    
    echo "Please run 'sudo tailscale up' after this script completes to connect to your Tailscale network."
fi

# Creating configuration backup
# echo "Creating configuration backup..."
# mkdir -p backups
# cp config.ini backups/config.ini.bak

# Setup complete
echo ""
echo "=== Setup Complete ==="
echo "You may need to reboot for all changes to take effect."
echo "To start the installation manually, run: ./run.sh"
echo "To start as a service: sudo systemctl start dans-le-blanc.service"
echo ""
echo "Don't forget to configure the IP addresses in config.ini!"
