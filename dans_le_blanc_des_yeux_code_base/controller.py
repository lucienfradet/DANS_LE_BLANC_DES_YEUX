"""
Main controller for the Dans le Blanc des Yeux installation.
Manages all components with proper error handling and cleanup.

Usage:
    python controller.py [--visualize] [--disable-video]
"""

import configparser
import time
import signal
import sys
import argparse
import os
from osc_handler import run_osc_handler
from motor import MotorController  # Import the MotorController class
from camera_manager import CameraManager  # Import the CameraManager class
from video_streamer import VideoStreamer  # Import the VideoStreamer class
from video_display import VideoDisplay  # Import the VideoDisplay class

def signal_handler(sig, frame):
    """Handle shutdown signals gracefully."""
    print("\nShutting down... Please wait.")
    if 'osc_handler' in globals():
        osc_handler.stop()
    if 'serial_handler' in globals():
        serial_handler.disconnect()
    if 'motor_controller' in globals():  # Add motor controller cleanup
        motor_controller.stop()
    if 'camera_manager' in globals():  # Add camera manager cleanup
        camera_manager.stop()
    if 'video_streamer' in globals():  # Add video streamer cleanup
        video_streamer.stop()
    if 'video_display' in globals():  # Add video display cleanup
        video_display.stop()
    if 'visualizer' in globals() and visualizer is not None:
        visualizer.stop()
    print("Shutdown complete.")
    sys.exit(0)

if __name__ == "__main__":
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Dans le Blanc des Yeux Controller')
    parser.add_argument('--visualize', action='store_true', help='Enable terminal visualization')
    parser.add_argument('--disable-video', action='store_true', help='Disable video components')
    args = parser.parse_args()
    
    # Initialize visualizer variable
    visualizer = None
    
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Load configuration
    config = configparser.ConfigParser()
    config.read('config.ini')
    
    try:
        # Get remote IP from config
        remote_ip = config['ip']['pi-ip']
        print(f"Using remote IP: {remote_ip}")
        
        # Start visualizer if requested
        if args.visualize:
            from debug_visualizer import run_visualizer
            print("Starting terminal visualizer...")
            visualizer = run_visualizer()
        
        # Start OSC handler
        osc_handler, serial_handler = run_osc_handler(remote_ip)
        
        # Initialize and start motor controller with parameters from config
        print("Starting motor controller...")
        
        # Get motor settings from config or use defaults if not present
        motor_params = {}
        if 'motor' in config:
            try:
                motor_params['required_duration'] = config.getfloat('motor', 'required_duration', fallback=0.8)
                motor_params['check_interval'] = config.getfloat('motor', 'check_interval', fallback=0.1)
                motor_params['motion_timeout'] = config.getfloat('motor', 'motion_timeout', fallback=2.0)
                print(f"Using motor settings from config: {motor_params}")
            except (ValueError, configparser.Error) as e:
                print(f"Error reading motor config: {e}. Using defaults.")
        
        # Create motor controller with config parameters
        motor_controller = MotorController(serial_handler, **motor_params)
        
        # Set minimum interval between movements if specified in config
        if 'motor' in config and 'movement_min_interval' in config['motor']:
            try:
                interval = config.getfloat('motor', 'movement_min_interval', fallback=0.5)
                motor_controller.movement_min_interval = interval
                print(f"Set movement_min_interval to {interval} seconds")
            except (ValueError, configparser.Error):
                pass
                
        motor_controller.start()
        
        # Initialize video components if not disabled
        if not args.disable_video:
            # Get video settings from config or use defaults
            video_params = {}
            if 'video' in config:
                try:
                    # Add any video-specific configuration here
                    video_params['internal_camera_id'] = config.getint('video', 'internal_camera_id', fallback=0)
                    video_params['use_external_picam'] = config.getboolean('video', 'use_external_picam', fallback=True)
                    print(f"Using video settings from config: {video_params}")
                except (ValueError, configparser.Error) as e:
                    print(f"Error reading video config: {e}. Using defaults.")
            
            # Check if display is available
            has_display = "DISPLAY" in os.environ and os.environ["DISPLAY"]
            if not has_display:
                print("WARNING: No display detected (DISPLAY environment variable not set)")
                print("Video display component may not work properly")
            
            # Initialize enhanced camera manager with improved error handling
            print("Starting camera manager...")
            camera_manager = CameraManager(
                internal_camera_id=video_params.get('internal_camera_id', 0),
                external_picam=video_params.get('use_external_picam', True),
                disable_missing=True,  # Continue even if cameras aren't available
                swap_cameras=True
            )
            if not camera_manager.start():
                print("Warning: Failed to start camera manager. Video functionality may be limited.")
            
            # Initialize video streamer
            print("Starting video streamer...")
            video_streamer = VideoStreamer(camera_manager, remote_ip)
            video_streamer.start()
            
            # Initialize video display
            if has_display:
                print("Starting video display...")
                try:
                    video_display = VideoDisplay(video_streamer, camera_manager)
                    video_display.start()
                except Exception as e:
                    print(f"Error starting video display: {e}")
                    print("Video display functionality will be limited")
            else:
                print("Skipping video display initialization (no display available)")
        else:
            print("Video components disabled by command line argument")
        
        # Keep main thread alive
        print("System running. Press Ctrl+C to exit.")
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\nUser requested shutdown.")
        signal_handler(None, None)
    except Exception as e:
        print(f"Fatal error: {str(e)}")
        try:
            # Try to clean up
            signal_handler(None, None)
        except:
            sys.exit(1)
