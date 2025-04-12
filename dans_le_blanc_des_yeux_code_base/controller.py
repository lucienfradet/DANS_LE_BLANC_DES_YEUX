"""
Main controller for the Dans le Blanc des Yeux installation.
Uses SSH console input to toggle the visualizer.

Usage:
    python controller.py [--visualize] [--disable-video] [--disable-audio] [--service]
"""

import configparser
import time
import signal
import sys
import argparse
import os
import threading
import subprocess

from system_state import system_state

# Initialize GStreamer BEFORE importing OpenCV-related modules
# This ensures GStreamer is initialized in the main thread
try:
    # default 2 (set to 4 or more for detail debugging)
    os.environ["GST_DEBUG"] = "2"

    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    Gst.init(None)
    GSTREAMER_AVAILABLE = True
    print("GStreamer initialized successfully")
except (ImportError, ValueError) as e:
    print(f"WARNING: GStreamer not available: {e}")
    print("Audio functionality will be limited")
    GSTREAMER_AVAILABLE = False

# Import other components after GStreamer is initialized
from osc_handler import run_osc_handler
from motor import MotorController
from camera_manager import CameraManager
from video_streamer import VideoStreamer
from video_display import VideoDisplay
from debug_visualizer import TerminalVisualizer

# Only import audio components if GStreamer is available
if GSTREAMER_AVAILABLE:
    from audio_streamer import AudioStreamer
    from audio_playback import AudioPlayback

# Global variables
visualizer = None
visualizer_active = False
stop_input_thread = False

def toggle_visualizer():
    """Toggle the visualizer on/off"""
    global visualizer, visualizer_active
    
    if visualizer is None:
        # Initialize visualizer if it doesn't exist.
        visualizer = TerminalVisualizer()
    
    if visualizer_active:
        print("\nToggling visualizer OFF")
        visualizer.stop()
        visualizer_active = False
    else:
        print("\nToggling visualizer ON")
        visualizer.start()
        visualizer_active = True
    
    # Print prompt again to make it clear we're waiting for input
    print("[v=toggle visualizer, q=quit]: ", end='', flush=True)

def input_monitor():
    """Thread that monitors for user input to toggle visualizer"""
    global stop_input_thread
    
    print("\nCommand prompt ready. Type and press Enter:")
    print("[v=toggle visualizer, q=quit]: ", end='', flush=True)
    
    while not stop_input_thread:
        try:
            # Read a single character
            key = input().lower().strip()
            if key == 'v':
                toggle_visualizer()
            elif key == 'q':
                print("\nQuitting application...")
                os.kill(os.getpid(), signal.SIGINT)
            elif key:  # Any other command
                print(f"Unknown command: '{key}'")
                print("[v=toggle visualizer, q=quit]: ", end='', flush=True)
        except Exception as e:
            if not stop_input_thread:  # Only log errors if we're still supposed to be running
                print(f"\nInput monitor error: {e}")
            time.sleep(0.5)

def signal_handler(sig, frame):
    """Handle shutdown signals gracefully with updated audio component handling."""
    global stop_input_thread
    print("\nShutting down... Please wait.")
    stop_input_thread = True
    
    # Stop all components in the correct order
    # First audio components - playback first, then streaming
    if 'audio_playback' in globals() and audio_playback is not None:
        try:
            print("Stopping audio playback...")
            audio_playback.stop()
            print("Audio playback stopped successfully")
            # Add small delay to ensure audio resources are released
            time.sleep(0.5)
        except Exception as e:
            print(f"Error stopping audio playback: {e}")
    
    if 'audio_streamer' in globals() and audio_streamer is not None:
        try:
            print("Stopping audio streamer...")
            audio_streamer.stop()
            print("Audio streamer stopped successfully")
            # Add small delay to ensure audio resources are released
            time.sleep(0.5)
        except Exception as e:
            print(f"Error stopping audio streamer: {e}")
    
    # Stop video components (unchanged)
    if 'video_display' in globals() and video_display is not None:
        try:
            video_display.stop()
            print("Video display stopped successfully")
        except Exception as e:
            print(f"Error stopping video display: {e}")
            
    if 'video_streamer' in globals() and video_streamer is not None:
        try:
            video_streamer.stop()
            print("Video streamer stopped successfully")
        except Exception as e:
            print(f"Error stopping video streamer: {e}")
            
    if 'camera_manager' in globals() and camera_manager is not None:
        try:
            camera_manager.stop()
            print("Camera manager stopped successfully")
        except Exception as e:
            print(f"Error stopping camera manager: {e}")
    
    # Stop motor controller
    if 'motor_controller' in globals() and motor_controller is not None:
        try:
            motor_controller.stop()
            print("Motor controller stopped successfully")
        except Exception as e:
            print(f"Error stopping motor controller: {e}")
    
    # Stop serial handler
    if 'serial_handler' in globals() and serial_handler is not None:
        try:
            serial_handler.disconnect()
            print("Serial handler stopped successfully")
        except Exception as e:
            print(f"Error stopping serial handler: {e}")
    
    # Stop OSC handler
    if 'osc_handler' in globals() and osc_handler is not None:
        try:
            osc_handler.stop()
            print("OSC handler stopped successfully")
        except Exception as e:
            print(f"Error stopping OSC handler: {e}")
    
    # Stop visualizer if it's running
    global visualizer, visualizer_active
    if visualizer is not None and visualizer_active:
        try:
            visualizer.stop()
            print("Visualizer stopped successfully")
        except Exception as e:
            print(f"Error stopping visualizer: {e}")
    
    print("Shutdown complete.")
    sys.exit(0)

def initialize_components():
    """Initialize all components of the system."""
    global osc_handler, serial_handler, motor_controller
    global camera_manager, video_streamer, video_display
    global audio_streamer, audio_playback
    
    # Set default values for components that might not be initialized
    audio_streamer = None
    audio_playback = None
    camera_manager = None
    video_streamer = None
    video_display = None
    
    # Load configuration
    config = configparser.ConfigParser()
    config.read('config.ini')
    
    # Get remote IP from config
    remote_ip = config['ip']['pi-ip']
    print(f"Using remote IP: {remote_ip}")

    # Set pressure debounce time if available in config
    if 'system' in config and 'pressure_debounce_time' in config['system']:
        try:
            debounce_time = config.getfloat('system', 'pressure_debounce_time', fallback=1.0)
            from system_state import system_state
            system_state.set_pressure_debounce_time(debounce_time)
            print(f"Set pressure debounce time to {debounce_time} seconds")
        except (ValueError, configparser.Error) as e:
            print(f"Error reading pressure_debounce_time from config: {e}. Using default.")
    
    # Start OSC handler
    print("Starting OSC handler...")
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

            # Y-axis transformation parameters
            motor_params['y_reverse'] = config.getboolean('motor', 'y_reverse', fallback=True)
            motor_params['y_min_input'] = config.getfloat('motor', 'y_min_input', fallback=-10)
            motor_params['y_max_input'] = config.getfloat('motor', 'y_max_input', fallback=60)
            motor_params['y_min_output'] = config.getfloat('motor', 'y_min_output', fallback=-30)
            motor_params['y_max_output'] = config.getfloat('motor', 'y_max_output', fallback=80)

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
    
    return remote_ip, config, (osc_handler, serial_handler, motor_controller)

def initialize_video_components(remote_ip, config, disable_video):
    """Initialize video components if not disabled."""
    global camera_manager, video_streamer, video_display
    
    # Default values
    camera_manager = None
    video_streamer = None
    video_display = None
    
    if disable_video:
        print("Video components disabled by command line argument")
        return None, None, None
    
    # Get video settings from config or use defaults
    video_params = {}
    if 'video' in config:
        try:
            # Add any video-specific configuration here
            video_params['internal_camera_id'] = config.getint('video', 'internal_camera_id', fallback=0)
            video_params['external_camera_id'] = config.getint('video', 'external_camera_id', fallback=1)
            print(f"Using video settings from config: {video_params}")
        except (ValueError, configparser.Error) as e:
            print(f"Error reading video config: {e}. Using defaults.")
    
    # Check if display is available
    has_display = "DISPLAY" in os.environ and os.environ["DISPLAY"]
    if not has_display:
        print("WARNING: No display detected (DISPLAY environment variable not set)")
        print("Video display component may not work properly")
    
    # Release any ALSA resources before initializing cameras
    if GSTREAMER_AVAILABLE:
        try:
            # Use subprocess to run ALSA force-reload to ensure clean state
            subprocess.run(["sudo", "alsa", "force-reload"], 
                           stdout=subprocess.DEVNULL, 
                           stderr=subprocess.DEVNULL)
            time.sleep(1)  # Wait for ALSA to reinitialize
        except Exception as e:
            print(f"Could not reset ALSA: {e}")
    
    # Initialize enhanced camera manager with improved error handling
    print("Starting camera manager...")
    camera_manager = CameraManager(
        internal_camera_id=video_params.get('internal_camera_id', 0),
        external_camera_id=video_params.get('external_camera_id', 1),
        disable_missing=True,  # Continue even if cameras aren't available
        internal_frame_width=1024,
        internal_frame_height=600,
        external_frame_width=1024,
        external_frame_height=600,
        enable_autofocus=True
    )
    if not camera_manager.start():
        print("Warning: Failed to start camera manager. Video functionality may be limited.")
        return None, None, None
    
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
    
    return camera_manager, video_streamer, video_display

def initialize_audio_components(remote_ip, config, disable_audio):
    """Initialize audio components if not disabled using the new persistent pipeline approach."""
    global audio_streamer, audio_playback
    
    # Default values
    audio_streamer = None
    audio_playback = None
    
    if disable_audio:
        print("Audio components disabled by command line argument")
        return None, None
    
    if not GSTREAMER_AVAILABLE:
        print("GStreamer not available - cannot initialize audio components")
        return None, None
    
    # Get audio settings from config or use defaults
    audio_params = {}
    if 'audio' in config:
        try:
            # Add any audio-specific configuration here
            print(f"Using audio settings from config: {audio_params}")
        except (ValueError, configparser.Error) as e:
            print(f"Error reading audio config: {e}. Using defaults.")
    
    # PulseAudio configuration check
    try:
        # Check if PulseAudio is running
        result = subprocess.run(["pulseaudio", "--check"], 
                              stdout=subprocess.PIPE, 
                              stderr=subprocess.PIPE)
        if result.returncode != 0:
            print("Starting PulseAudio server...")
            subprocess.run(["pulseaudio", "--start"], 
                          stdout=subprocess.PIPE, 
                          stderr=subprocess.PIPE)
            time.sleep(1)  # Give it time to start
    except Exception as e:
        print(f"PulseAudio check/start failed: {e}")
    
    # Initialize audio streamer (must be started first)
    print("Starting persistent audio streamer...")
    try:
        audio_streamer = AudioStreamer(remote_ip)
        if not audio_streamer.start():
            print("Warning: Failed to start audio streamer. Audio functionality may be limited.")
            return None, None
    except Exception as e:
        print(f"Error starting audio streamer: {e}")
        print("Audio functionality will be limited")
        return None, None
    
    # Wait for streamer to initialize fully
    time.sleep(0.5)
    
    # Initialize audio playback (after streamer is ready)
    print("Starting persistent audio playback...")
    try:
        audio_playback = AudioPlayback(audio_streamer)
        if not audio_playback.start():
            print("Warning: Failed to start audio playback. Audio functionality may be limited.")
            # If playback fails, stop the streamer
            if audio_streamer:
                audio_streamer.stop()
            return None, None
    except Exception as e:
        print(f"Error starting audio playback: {e}")
        print("Audio playback functionality will be limited")
        if audio_streamer:
            audio_streamer.stop()
        return None, None
    
    print("Audio components successfully initialized with persistent pipelines")
    return audio_streamer, audio_playback

if __name__ == "__main__":
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Dans le Blanc des Yeux Controller')
    parser.add_argument('--visualize', action='store_true', help='Enable terminal visualization')
    parser.add_argument('--disable-video', action='store_true', help='Disable video components')
    parser.add_argument('--disable-audio', action='store_true', help='Disable audio components')
    args = parser.parse_args()
    
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Initialize visualizer but only start if requested via command line
        visualizer = TerminalVisualizer()
        visualizer_active = False
        
        if args.visualize:
            print("Starting terminal visualizer...")
            visualizer.start()
            visualizer_active = True
        
        # Start input monitor thread
        input_thread = threading.Thread(target=input_monitor)
        input_thread.daemon = True
        input_thread.start()
        
        # Initialize core components
        remote_ip, config, core_components = initialize_components()
        
        # Keep references to avoid garbage collection
        osc_handler, serial_handler, motor_controller = core_components
        
        # Initialize audio first with new persistent pipeline implementation
        if not args.disable_audio:
            print("Initializing persistent audio components...")
            audio_streamer, audio_playback = initialize_audio_components(remote_ip, config, args.disable_audio)
            # Add longer delay to allow audio components to initialize properly
            time.sleep(1.5)
        
        # Initialize video components after audio
        if not args.disable_video:
            camera_manager, video_streamer, video_display = initialize_video_components(remote_ip, config, args.disable_video)
        
        # Keep main thread alive while the input thread handles commands
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
