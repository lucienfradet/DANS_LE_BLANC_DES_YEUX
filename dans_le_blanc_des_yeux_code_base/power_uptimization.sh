#!/bin/bash
# Combined Power Optimization Script for Raspberry Pi 5

# Check if running as root
if [ "$(id -u)" -ne 0 ]; then
  echo "This script must be run as root"
  exit 1
fi

echo "==== Raspberry Pi 5 Power Optimization ===="
echo ""

# === PART 1: Immediate power savings (runtime changes) ===
echo "Applying immediate power-saving settings..."

# Safely disable WiFi without affecting Ethernet
ip link set wlan0 down 2>/dev/null || echo "WiFi already disabled or not available"

# Disable Bluetooth
rfkill block bluetooth

# Disable services
systemctl disable --now bluetooth.service
systemctl disable --now wpa_supplicant.service
systemctl disable --now avahi-daemon.service

# Set CPU governor to powersave
echo "powersave" | tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor

echo "Immediate power-saving settings applied!"
echo ""

# === PART 2: Boot-time configurations (require reboot) ===
echo "Checking boot configuration settings..."

CONFIG_FILE="/boot/firmware/config.txt"
if [ ! -f "$CONFIG_FILE" ]; then
    CONFIG_FILE="/boot/config.txt"  # Fallback for older Pi OS versions
fi

# Function to check if a line exists in config.txt and add it if not
add_if_not_exists() {
    if ! grep -q "^$1" "$CONFIG_FILE"; then
        echo "$1" >> "$CONFIG_FILE"
        echo "Added: $1"
    else
        echo "Already configured: $1"
    fi
}

# Backup original config
cp "$CONFIG_FILE" "${CONFIG_FILE}.backup.$(date +%Y%m%d%H%M%S)"

# Add power-saving configurations
add_if_not_exists "# Power savings for Pi 5"
add_if_not_exists "dtoverlay=disable-wifi"
add_if_not_exists "dtoverlay=disable-bt"
add_if_not_exists "# Reduce GPU memory (minimum safe value)"
add_if_not_exists "gpu_mem=128"
add_if_not_exists "# Moderate CPU underclock"
add_if_not_exists "arm_freq=2000"
add_if_not_exists "# Reduce voltage slightly"
add_if_not_exists "over_voltage=-1"

echo ""
echo "Boot configuration updated!"
echo "You'll need to reboot for these changes to take effect."
echo "Run 'sudo reboot' when ready."
