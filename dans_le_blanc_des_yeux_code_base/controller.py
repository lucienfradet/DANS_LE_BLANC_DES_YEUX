"""
Main controller for the Dans le Blanc des Yeux installation.
Uses SSH console input to toggle the visualizer.

Usage:
    python controller.py [--visualize] [--disable-video] [--disable-audio]
"""

import configparser
import time
import signal
import sys
import argparse
import os
import threading
import audio_system
from osc_handler import run_osc_handler
from motor import MotorController
from camera_manager import CameraManager
from video_streamer import VideoStreamer
from video_display import VideoDisplay
from audio_system import AudioSystem
from debug_visualizer import TerminalVisualizer

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
    """Handle shutdown signals gracefully."""
    global stop_input_thread
    print("\nShutting down... Please wait.")
    stop_input_thread = True
    
    # First, set stop flags on all components without waiting for joins
    if 'audio_system' in globals():
        try:
            # Just set stop flags without waiting for threads
            audio_system.running = False
            audio_system.stop_event.set()
        except Exception as e:
            print(f"Error signaling audio system: {e}")
    
    if 'osc_handler' in globals():
        try:
            osc_handler.running = False
            if hasattr(osc_handler, 'stop_event'):
                osc_handler.stop_event.set()
        except Exception as e:
            print(f"Error signaling OSC handler: {e}")
    
    if 'video_streamer' in globals():
        try:
            video_streamer.running = False
        except Exception as e:
            print(f"Error signaling video streamer: {e}")
    
    if 'video_display' in globals():
        try:
            video_display.running = False
        except Exception as e:
            print(f"Error signaling video display: {e}")
    
    if 'motor_controller' in globals():
        try:
            motor_controller.running = False
        except Exception as e:
            print(f"Error signaling motor controller: {e}")
    
    # Wait a moment for threads to notice stop flags
    time.sleep(0.5)
    
    # Now actually stop components in a specific order
    # Stop visualization first as it's least critical
    global visualizer, visualizer_active
    if visualizer is not None and visualizer_active:
        try:
            visualizer.stop()
        except Exception as e:
            print(f"Error stopping visualizer: {e}")
    
    # Stop components in dependency order
    components_to_stop = [
        ('camera_manager', 'stop'),
        ('video_display', 'stop'),
        ('video_streamer', 'stop'),
        ('audio_system', 'stop'),
        ('motor_controller', 'stop'),
        ('serial_handler', 'disconnect'),
        ('osc_handler', 'stop')
    ]
    
    for component_name, method_name in components_to_stop:
        if component_name in globals():
            try:
                component = globals()[component_name]
                method = getattr(component, method_name)
                method()
            except Exception as e:
                print(f"Error stopping {component_name}: {e}")
    
    print("Shutdown complete.")
    sys.exit(0)

def stop_thread_gracefully(thread, timeout=1.0):
    """Helper function to stop a thread gracefully with timeout."""
    if thread is not None and thread.is_alive():
        try:
            thread.join(timeout=timeout)
            if thread.is_alive():
                print(f"Warning: Thread {thread.name} did not terminate within timeout")
                return False
            return True
        except Exception as e:
            print(f"Error stopping thread: {e}")
            return False
    return True
