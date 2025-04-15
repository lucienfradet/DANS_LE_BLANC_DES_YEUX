# Dans le Blanc des Yeux

## Project Overview
Dans le Blanc des Yeux is an interactive art installation that uses Raspberry Pi devices to create a connection between two physical spaces. When a user applies pressure to one device, the other device mirrors its orientation, creating a sense of presence across distance.

## System Components

### Core Components
- **Motor Control**: Controls the physical orientation (tilt/pan) based on remote device state
- **Video Streaming**: Captures and displays camera feeds between devices
- **Audio Streaming**: Provides audio communication between devices
- **System State Management**: Tracks and synchronizes state between devices
- **OSC Communication**: Handles network communication between devices

### Hardware Requirements
- Raspberry Pi (preferably Pi 5)
- Camera modules (internal and external)
- Audio capture and playback hardware
- Servomotors for orientation control
- Pressure sensor

## Installation & Setup

### Initial Setup
1. Clone this repository
2. Run the setup script:
```bash
# not fully up to date!
sudo ./setup.sh

# If you need wifi, make sure you remove this line from /boot/firmware/config.txt
dtoverlay=disable-wifi
```

3. Configure `config.ini` with the correct IP addresses and hardware settings
4. Test the system:
```bash
./run.sh visual
```

### Creating a System Service
The installation provides a systemd service for auto-starting on boot:

```bash
# Enable service to start on boot
sudo systemctl enable dans-le-blanc.service

# Manually start the service
sudo systemctl start dans-le-blanc.service
```

## Network Configuration

### Tailscale Setup
For reliable networking across different networks:
1. Install Tailscale: `curl -fsSL https://tailscale.com/install.sh | sh`
2. Set up Tailscale: `sudo tailscale up`
3. Configure the IP in `config.ini` to use the Tailscale IP

### Wifi Control
To force LTE/disable wifi:
```bash
sudo rfkill block wifi      # Block wifi
sudo rfkill unblock wifi    # Unblock wifi
```

## Usage

### Run Options
```bash
./run.sh [options]
```

Options:
- `visual`: Enable terminal visualization
- `disable-video`: Run without video components
- `disable-audio`: Run without audio components
- `service`: Run in service mode (no input monitor)

### Runtime Controls
When running interactively:
- Type `v` + Enter to toggle the terminal visualizer
- Type `q` + Enter to quit the application
- Press Ctrl+C to stop the application

## Configuration (config.ini)

### System Settings
```ini
[system]
pressure_debounce_time = 1.0  # Time to wait before accepting pressure changes
```

### Network Configuration
```ini
[ip]
pi-ip = xxx.xxx.xxx.xxx  # Remote device IP address
```

### Motor Settings
```ini
[motor]
check_interval = 0.1            # How often to check system state
motion_timeout = 0.5            # How long motors are considered "moving"
movement_min_interval = 0.5     # Minimum time between movements
y_reverse = true                # Reverse Y-axis motion
y_min_input = -10               # Y-axis input range
y_max_input = 60
y_min_output = -30              # Y-axis output range
y_max_output = 80
```

### Video Settings
```ini
[video]
internal_camera_id = 0          # Camera device IDs
external_camera_id = 1
display_width = 1024            # Display resolution
display_height = 600
```

Camera rotation options: `0`, `90_clockwise`, `90_counter`, `180`

### Audio Settings
```ini
[audio]
personal_mic_name = KTMicro TX 96Khz    # Audio device names
global_mic_name = USB Audio Device
personal_mic_gain = 65                  # Microphone gain levels (0-100)
global_mic_gain = 75
```

## System Monitoring and Control

### Service Management
```bash
# Stop the service
sudo systemctl stop dans-le-blanc.service

# Start the service
sudo systemctl start dans-le-blanc.service

# Restart the service
sudo systemctl restart dans-le-blanc.service

# Check status
sudo systemctl status dans-le-blanc.service
```

### Log Viewing
```bash
# View all logs for the service
journalctl -u dans-le-blanc.service

# Follow live logs (like tail -f)
journalctl -f -u dans-le-blanc.service

# View only recent logs
journalctl -u dans-le-blanc.service -n 100

# View logs since boot
journalctl -u dans-le-blanc.service -b
```

## Architecture Overview

### Audio Streaming Logic
1. When both have pressure:
   - Stream personal mic to remote device
   - Play with LEFT channel muted
   - Receive from personal mic stream

2. When remote has pressure and local doesn't:
   - Stream global mic to remote device
   - Play with RIGHT channel muted
   - Receive from global mic stream

3. When local has pressure and remote doesn't:
   - Stream personal mic to remote device
   - Play with LEFT channel muted
   - Receive from personal mic stream

4. When neither has pressure:
   - No streaming (both pipelines paused)

### Video Streaming Logic
1. When remote device has pressure and local doesn't:
   - Send external camera feed

2. When local device has pressure and remote doesn't:
   - Receive remote's external camera feed

3. When both have pressure:
   - Send internal camera feed
   - Receive remote's internal camera feed

4. When neither has pressure:
   - No streaming

### Motor Control Logic
The system moves motors when the remote device has pressure but the local device doesn't, causing the local device to match the orientation of the remote device.

## Troubleshooting

### Common Issues and Solutions

1. **Audio device not found:**
   - Check device connections
   - Run `pactl list sources short` to list audio devices
   - Update input device names in config.ini
   - Run `pacmd list-sinks` to list audio outputs and find the name of the port under `ports:`
   - Update the default audio output for PulseAudio in `/etc/pulse/default.pa`
   - Example config:
   ```bash
    # Make headphones default
   set-default-sink 0
   set-sink-port 0 analog-output-headphones
   ```

2. **Camera not working:**
   - Check camera connections
   - Run `v4l2-ctl --list-devices` to list camera devices
   - Update camera IDs in config.ini

3. **Network connection issues:**
   - Verify Tailscale is running: `tailscale status`
   - Check IP addresses in config.ini
   - Check firewalls aren't blocking UDP ports 5000, 5001, 6000, 6001, 8888

4. **Motor not moving:**
   - Check Arduino connection
   - Verify serial device: `ls -l /dev/ttyACM0`
   - Check motor logs: `journalctl -f -u dans-le-blanc.service | grep "Motor"`

5. **Service fails to start:**
   - Check logs: `journalctl -xe -u dans-le-blanc.service`
   - Verify permissions: `sudo chmod +x run.sh`

### Performance Optimization
For better performance on Raspberry Pi, you can run:
```bash
sudo ./power_optimization.sh
```

This will optimize power settings and disable unnecessary services.

## Component Descriptions

### audio_playback.py
Manages audio playback with GStreamer, handling channel muting based on system state.

### audio_streamer.py
Handles audio capture and streaming between devices using GStreamer.

### camera_manager.py
Manages Pi camera modules, handles camera detection and frame capture.

### controller.py
Main system controller that initializes and manages all components.

### debug_visualizer.py
Provides terminal-based visualization for system state.

### motor.py
Controls motors based on system state to match remote device orientation.

### osc_handler.py
Handles OSC communication between devices over the network.

### serial_handler.py
Manages communication with the Arduino for motor control.

### system_state.py
Central state management with observer pattern for component synchronization.

### video_display.py
Manages video display based on system state.

### video_streamer.py
Handles video streaming between devices using H.265 encoding over GStreamer.

## License and Credits
This project was created for "Dans le Blanc des Yeux" art installation.
