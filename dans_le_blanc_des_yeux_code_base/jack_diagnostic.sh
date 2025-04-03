#!/bin/bash
# Audio diagnostics script for Dans le Blanc des Yeux installation
# This script checks audio configuration and helps troubleshoot JACK issues

echo "=== Dans le Blanc des Yeux Audio Diagnostics ==="
echo ""

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check for root permissions
if [ "$(id -u)" -ne 0 ]; then
    echo "Note: Some tests may require root permissions."
    echo "Consider running with sudo if you encounter permission errors."
    echo ""
fi

# Check dependencies
echo "=== Checking dependencies ==="
missing_deps=0

# Essential audio packages
for package in jackd jack-tools libjack-jackd2-dev alsa-utils zita-njbridge python3-pip; do
    if dpkg -l | grep -q "ii  $package"; then
        echo "✅ $package installed"
    else
        echo "❌ $package missing"
        missing_deps=1
    fi
done

# Check Python JACK client
if pip3 list | grep -q "JACK-Client"; then
    echo "✅ Python JACK client installed"
else
    echo "❌ Python JACK client missing"
    missing_deps=1
fi

if [ $missing_deps -ne 0 ]; then
    echo ""
    echo "Some dependencies are missing. Install them with:"
    echo "sudo apt install jackd jack-tools libjack-jackd2-dev alsa-utils zita-njbridge"
    echo "pip3 install JACK-client"
    echo ""
fi

# Check audio hardware
echo ""
echo "=== Audio Hardware ==="
echo "Playback devices:"
aplay -l

echo ""
echo "Recording devices:"
arecord -l

# Check for TX 96Khz and USB Audio Device
echo ""
echo "=== Configured Devices ==="

if arecord -l | grep -i "TX 96Khz"; then
    echo "✅ TX 96Khz device found"
else
    echo "❌ TX 96Khz device NOT found"
fi

if arecord -l | grep -i "USB Audio Device"; then
    echo "✅ USB Audio Device found"
else
    echo "❌ USB Audio Device NOT found"
fi

# Check permissions
echo ""
echo "=== User Permissions ==="
current_user=$(whoami)
echo "Current user: $current_user"

# Check if user is in audio group
if groups $current_user | grep -q "\baudio\b"; then
    echo "✅ User is in the audio group"
else
    echo "❌ User is NOT in the audio group"
    echo "  Fix with: sudo usermod -a -G audio $current_user"
    echo "  (logout and login required for changes to take effect)"
fi

# Check JACK status
echo ""
echo "=== JACK Status ==="

if command_exists jackd; then
    echo "✅ JACK is installed"
    
    # Check if JACK is running
    if pgrep -x "jackd" > /dev/null; then
        echo "✅ JACK server is running"
        
        # Get JACK process details
        jack_pid=$(pgrep -x "jackd")
        echo "   PID: $jack_pid"
        
        jack_cmd=$(ps -p $jack_pid -o command= 2>/dev/null || echo "Command not available")
        echo "   Command: $jack_cmd"
        
        # Check if we can get JACK stats
        if command_exists jack_control; then
            jack_status=$(jack_control status 2>&1)
            echo "   Status: $jack_status"
            
            if command_exists jack_lsp; then
                echo ""
                echo "   Connected ports:"
                jack_lsp -c
            fi
        fi
    else
        echo "❌ JACK server is NOT running"
        echo "   Starting JACK for testing..."
        
        # Try starting JACK with different configurations
        for config in "jackd -d alsa -r 48000 -p 1024 -n 2" "jackd -d alsa -d hw:0 -r 44100 -p 512 -n 2" "jackd -d dummy"; do
            echo ""
            echo "   Trying: $config"
            $config &
            JACK_PID=$!
            sleep 2
            
            if kill -0 $JACK_PID 2>/dev/null; then
                echo "   ✅ JACK started successfully with this configuration"
                # Check if we can connect
                if command_exists jack_lsp && jack_lsp &>/dev/null; then
                    echo "   ✅ JACK client connection works"
                else
                    echo "   ❌ JACK client connection failed"
                fi
                
                # Kill the test JACK instance
                kill $JACK_PID
                sleep 1
                break
            else
                echo "   ❌ Failed to start JACK with this configuration"
            fi
        done
    fi
else
    echo "❌ JACK is NOT installed"
fi

# Test ALSA channel muting
echo ""
echo "=== Testing ALSA Channel Muting ==="

# Get master control name
if command_exists amixer; then
    # Try to identify the master control
    controls=$(amixer scontrols)
    echo "Available controls: $controls"
    
    # Test left/right channel muting
    echo ""
    echo "Testing left channel mute..."
    amixer sset Master mute &>/dev/null
    amixer sset Master unmute &>/dev/null
    
    for channel in "left" "right"; do
        echo "Trying to mute $channel channel..."
        if amixer sset Master $channel mute &>/dev/null; then
            echo "✅ Successfully muted $channel channel"
            # Unmute it again
            amixer sset Master $channel unmute &>/dev/null
        else
            echo "❌ Failed to mute $channel channel with amixer"
            echo "   This might affect channel muting functionality"
        fi
    done
else
    echo "amixer not found, cannot test channel muting"
fi

# Test network connectivity for audio streaming
echo ""
echo "=== Network Configuration ==="
echo "IP Addresses:"
hostname -I

# Load remote IP from config
if [ -f "config.ini" ]; then
    remote_ip=$(grep -oP 'pi-ip\s*=\s*\K[0-9.]+' config.ini)
    if [ -n "$remote_ip" ]; then
        echo "Remote IP from config: $remote_ip"
        
        # Test connectivity
        echo "Testing connectivity to remote device..."
        if ping -c 1 -W 2 $remote_ip &>/dev/null; then
            echo "✅ Remote device is reachable"
        else
            echo "❌ Cannot reach remote device at $remote_ip"
            echo "   This will prevent audio streaming"
        fi
    else
        echo "Remote IP not found in config.ini"
    fi
else
    echo "config.ini not found"
fi

# Summary
echo ""
echo "=== Audio Diagnostics Summary ==="

# Check for critical issues
if [ $missing_deps -ne 0 ]; then
    echo "❌ Missing dependencies"
else
    echo "✅ All dependencies installed"
fi

# Check for device issues
if ! arecord -l | grep -i "TX 96Khz" > /dev/null || ! arecord -l | grep -i "USB Audio Device" > /dev/null; then
    echo "❌ One or more configured audio devices not found"
else
    echo "✅ All configured audio devices found"
fi

# Check for permission issues
if ! groups $current_user | grep -q "\baudio\b"; then
    echo "❌ User permission issues (not in audio group)"
else
    echo "✅ User permissions correct"
fi

# Check for JACK issues
if ! pgrep -x "jackd" > /dev/null && command_exists jackd; then
    echo "❌ JACK server not running"
elif ! command_exists jackd; then
    echo "❌ JACK not installed"
else
    echo "✅ JACK server running"
fi

echo ""
echo "If you encounter audio issues:"
echo "1. Run 'sudo apt install --reinstall jackd2 libjack-jackd2-dev'"
echo "2. Add user to audio group: 'sudo usermod -a -G audio $USER'"
echo "3. Restart the system and try again"
echo "4. If problems persist, try using the fallback mode in audio_system.py"
echo "   (The fallback mode uses direct ALSA controls without JACK)"
echo ""
echo "=== End of Audio Diagnostics ==="
