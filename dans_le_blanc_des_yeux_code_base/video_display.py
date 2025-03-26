"""
Video display module for the Dans le Blanc des Yeux installation.
Handles displaying video streams based on pressure state.

Logic:
1. No pressure on either device: Display nothing (black screen)
2. Local pressure: Display remote external camera video
3. Remote pressure: Display nothing
4. Both have pressure: Display remote internal camera video
"""

import os
import time
import threading
import cv2
import numpy as np
import configparser
from typing import Dict, Optional, Tuple, List

from system_state import system_state
from camera_manager import CameraManager
from video_streamer import VideoStreamer

class VideoDisplay:
    """Handles displaying video streams on the physical display."""
    
    def __init__(self, video_streamer: VideoStreamer, camera_manager: CameraManager):
        self.video_streamer = video_streamer
        self.camera_manager = camera_manager
        
        # Display parameters for Waveshare 7-inch display
        self.window_width = 1280
        self.window_height = 800
        
        # Default rotation settings (will be overridden by config if available)
        self.internal_rotation = '0'  # No rotation by default
        self.external_rotation = '90_counter'  # 90 degrees counterclockwise by default
        
        # Load rotation settings from config.ini
        self._load_config()
        
        # Set environment variable to ensure display on the physical screen
        # This helps when running over SSH
        if "DISPLAY" not in os.environ:
            os.environ["DISPLAY"] = ":0"
        
        # Threading
        self.running = False
        self.thread = None
        
        # Window name
        self.window_name = "Dans le Blanc des Yeux"
        
        # Register for frame update callbacks
        self.video_streamer.register_internal_frame_callback(self._on_internal_frame_update)
        self.video_streamer.register_external_frame_callback(self._on_external_frame_update)
        
        # Register as observer for state changes
        system_state.add_observer(self._on_state_change)
        
        # Frame update flags
        self.internal_frame_updated = threading.Event()
        self.external_frame_updated = threading.Event()
        
        print("Video display initialized")
        print(f"Using rotations - Internal: {self.internal_rotation}, External: {self.external_rotation}")
    
    def _load_config(self):
        """Load display settings from config.ini"""
        try:
            config = configparser.ConfigParser()
            config.read('config.ini')
            
            if 'video' in config:
                # Read internal camera rotation setting
                if 'internal_rotation' in config['video']:
                    self.internal_rotation = config['video']['internal_rotation']
                    
                # Read external camera rotation setting
                if 'external_rotation' in config['video']:
                    self.external_rotation = config['video']['external_rotation']
                    
                # Read display dimensions if specified
                if 'display_width' in config['video']:
                    self.window_width = config.getint('video', 'display_width', fallback=1280)
                if 'display_height' in config['video']:
                    self.window_height = config.getint('video', 'display_height', fallback=800)
                    
                print(f"Loaded display settings from config.ini")
            else:
                print("No [video] section found in config.ini, using default settings")
                # Add default rotation settings to config
                self._add_default_config_settings(config)
                
        except Exception as e:
            print(f"Error loading config: {e}")
            print("Using default rotation settings")
    
    def _add_default_config_settings(self, config):
        """Add default rotation settings to config.ini if not present"""
        try:
            if 'video' not in config:
                config['video'] = {}
            
            # Only add settings if they don't exist
            if 'internal_rotation' not in config['video']:
                config['video']['internal_rotation'] = self.internal_rotation
            if 'external_rotation' not in config['video']:
                config['video']['external_rotation'] = self.external_rotation
            if 'display_width' not in config['video']:
                config['video']['display_width'] = str(self.window_width)
            if 'display_height' not in config['video']:
                config['video']['display_height'] = str(self.window_height)
                
            # Add rotation options as comments
            config['video']['# Rotation options'] = '0, 90_clockwise, 90_counter, 180'
            
            # Write to config file
            with open('config.ini', 'w') as configfile:
                config.write(configfile)
                
            print("Added default rotation settings to config.ini")
        except Exception as e:
            print(f"Error adding default settings to config: {e}")
    
    def start(self) -> bool:
        """Start the video display system."""
        print("Starting video display...")
        
        # Try to create window with proper fullscreen setup
        try:
            # First destroy any existing windows with the same name
            cv2.destroyWindow(self.window_name)
            
            # Create window with specific flags
            cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
            
            # Set window size to match screen size
            cv2.resizeWindow(self.window_name, self.window_width, self.window_height)
            
            # Set window position to top-left corner
            cv2.moveWindow(self.window_name, 0, 0)
            
            # Make sure the window is visible with black frame
            black_frame = np.zeros((self.window_height, self.window_width, 3), dtype=np.uint8)
            cv2.imshow(self.window_name, black_frame)
            cv2.waitKey(1)
            
            # Set fullscreen AFTER showing the window
            cv2.setWindowProperty(self.window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
            
            # Try to set window to stay on top
            cv2.setWindowProperty(self.window_name, cv2.WND_PROP_TOPMOST, 1)
            
            # Wait a moment for window to settle
            time.sleep(0.5)
            
            print("Window created in fullscreen mode")
        except Exception as e:
            print(f"Warning: Could not create window: {e}")
            print("Display may not be available. Continuing anyway...")
        
        self.running = True
        
        # Start display thread
        self.thread = threading.Thread(target=self._display_loop)
        self.thread.daemon = True
        self.thread.start()
        
        print("Video display started")
        return True
    
    def stop(self) -> None:
        """Stop the video display system."""
        print("Stopping video display...")
        self.running = False
        
        if self.thread:
            self.thread.join(timeout=1.0)
        
        # Close all OpenCV windows
        cv2.destroyAllWindows()
        
        print("Video display stopped")
    
    def _on_state_change(self, changed_state: str) -> None:
        """Handle system state changes."""
        if changed_state in ["local", "remote"]:
            # Force a display update when pressure states change
            self.internal_frame_updated.set()
            self.external_frame_updated.set()
    
    def _should_display_video(self) -> Tuple[bool, str, Optional[np.ndarray]]:
        """
        Determine if video should be displayed based on pressure states.
        
        Returns:
            Tuple of (should_display, source_description, frame_to_display)
        """
        local_state = system_state.get_local_state()
        remote_state = system_state.get_remote_state()
        
        # No pressure on either device: Display nothing (black screen)
        if not local_state.get("pressure", False) and not remote_state.get("pressure", False):
            return (False, "No Display (No Pressure)", None)
            
        # Local pressure: Display remote external camera video
        elif local_state.get("pressure", False) and not remote_state.get("pressure", False):
            return (True, "Remote External Camera", self.video_streamer.get_received_external_frame())
            
        # Remote pressure: Display nothing
        elif not local_state.get("pressure", False) and remote_state.get("pressure", False):
            return (False, "No Display (Remote Pressure)", None)
            
        # Both have pressure: Display remote internal camera video
        elif local_state.get("pressure", False) and remote_state.get("pressure", False):
            return (True, "Remote Internal Camera", self.video_streamer.get_received_internal_frame())
            
        # Default case
        return (False, "No Display (Default)", None)
    
    def _on_internal_frame_update(self, frame: np.ndarray) -> None:
        """Handle new internal frame from remote device."""
        self.internal_frame_updated.set()
    
    def _on_external_frame_update(self, frame: np.ndarray) -> None:
        """Handle new external frame from remote device."""
        self.external_frame_updated.set()
        
    def _rotate_and_fit_frame(self, frame: np.ndarray, rotation_type: str, is_external: bool = False) -> np.ndarray:
        """
        Rotate frame and fit it to the display without stretching.
        
        Args:
            frame: The input frame to process
            rotation_type: Type of rotation ('90_clockwise', '90_counter', '180', '0')
            is_external: Whether this is the external camera feed
            
        Returns:
            Processed frame that fits the display without stretching
        """
        if frame is None:
            return None
            
        # Apply the appropriate rotation
        if rotation_type == '90_clockwise':
            # 90 degrees clockwise rotation
            rotated = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif rotation_type == '90_counter':
            # 90 degrees counter-clockwise rotation
            rotated = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        elif rotation_type == '180':
            # 180 degrees rotation
            rotated = cv2.rotate(frame, cv2.ROTATE_180)
        else:
            # No rotation (or invalid rotation type)
            rotated = frame.copy()
            
        return rotated
    
    def _display_loop(self) -> None:
        """Main display loop."""
        print("Display loop started")
        
        # Check if display is available
        display_available = "DISPLAY" in os.environ
        if not display_available:
            print("Warning: DISPLAY environment variable not set. Running in headless mode.")
        
        try:
            last_render_time = 0
            frame_counter = 0
            
            # Create a black frame (for when nothing should be displayed)
            black_frame = np.zeros((self.window_height, self.window_width, 3), dtype=np.uint8)
            
            # Force the window to be shown initially with a black frame
            if display_available:
                cv2.imshow(self.window_name, black_frame)
                cv2.waitKey(1)
                
                # Make sure window is in fullscreen mode
                cv2.setWindowProperty(self.window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
            
            last_state_debug = None
            
            while self.running:
                current_time = time.time()
                
                # Limit update rate to 30fps
                if current_time - last_render_time >= 0.033:  # ~30fps
                    # Determine what to display based on pressure states
                    should_display, source_desc, frame = self._should_display_video()
                    
                    # Generate state description for debugging
                    local_state = system_state.get_local_state()
                    remote_state = system_state.get_remote_state()
                    state_debug = f"Local pressure: {local_state.get('pressure', False)}, Remote pressure: {remote_state.get('pressure', False)}"
                    
                    # Log state changes for debugging
                    if state_debug != last_state_debug:
                        print(f"Display state change: {state_debug} - Display mode: {source_desc}")
                        last_state_debug = state_debug
                    
                    if should_display and frame is not None:
                        # Log that we're displaying a frame
                        frame_counter += 1
                        if frame_counter % 100 == 0:
                            print(f"Displaying video frame {frame_counter}: {source_desc} ({frame.shape[1]}x{frame.shape[0]})")
                        
                        # Apply rotation based on camera type using config settings
                        if "External" in source_desc:
                            # External camera - use config setting
                            display_frame = self._rotate_and_fit_frame(frame, self.external_rotation, is_external=True)
                            rotation_text = f"External ({self.external_rotation})"
                        else:
                            # Internal camera - use config setting
                            display_frame = self._rotate_and_fit_frame(frame, self.internal_rotation, is_external=False)
                            rotation_text = f"Internal ({self.internal_rotation})"
                        
                        # Add source label and rotation info to the top-left corner
                        cv2.putText(display_frame, f"{source_desc} - {rotation_text}", (10, 30), 
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
                    else:
                        # Use a completely black frame
                        display_frame = black_frame.copy()
                        
                        # Add explanation text only if there's something to explain
                        if source_desc:
                            cv2.putText(display_frame, source_desc, (10, 30), 
                                      cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
                    
                    # Show display if available
                    if display_available:
                        cv2.imshow(self.window_name, display_frame)
                        cv2.waitKey(1)
                        
                        # Make sure fullscreen is maintained
                        if frame_counter % 30 == 0:  # Check every second or so
                            cv2.setWindowProperty(self.window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
                    
                    last_render_time = current_time
                    
                    # Reset update flags
                    self.internal_frame_updated.clear()
                    self.external_frame_updated.clear()
                
                # Wait for frame updates or timeout
                self.internal_frame_updated.wait(timeout=0.01)
                self.external_frame_updated.wait(timeout=0.01)
        
        except Exception as e:
            print(f"Error in display loop: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if display_available:
                cv2.destroyAllWindows()
            print("Display loop stopped")
