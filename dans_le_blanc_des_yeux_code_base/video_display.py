"""
Enhanced video display module for the Dans le Blanc des Yeux installation.
Handles displaying video streams based on pressure state with improved
frame positioning, cropping, scaling and rotation.

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
from typing import Dict, Optional, Tuple, List, Any

from system_state import system_state
from camera_manager import CameraManager
from video_streamer import VideoStreamer

class VideoDisplay:
    """Handles displaying video streams on the physical display with enhanced controls."""
    
    def __init__(self, video_streamer: VideoStreamer, camera_manager: CameraManager):
        self.video_streamer = video_streamer
        self.camera_manager = camera_manager
        
        # Display parameters for Waveshare 7-inch display (default values)
        self.window_width = 1280
        self.window_height = 800
        
        # Default camera settings
        self.camera_settings = {
            'internal': {
                'rotation': '0',         # '0', '90_clockwise', '90_counter', '180'
                'position_x': 0,         # Horizontal position on screen
                'position_y': 0,         # Vertical position on screen
                'scale': 1.0,            # Scaling factor
                'crop_left': 0,          # Pixels to crop from left
                'crop_right': 0,         # Pixels to crop from right
                'crop_top': 0,           # Pixels to crop from top
                'crop_bottom': 0         # Pixels to crop from bottom
            },
            'external': {
                'rotation': '90_counter', # '0', '90_clockwise', '90_counter', '180'
                'position_x': 0,          # Horizontal position on screen
                'position_y': 0,          # Vertical position on screen
                'scale': 1.0,             # Scaling factor
                'crop_left': 0,           # Pixels to crop from left
                'crop_right': 0,          # Pixels to crop from right
                'crop_top': 0,            # Pixels to crop from top
                'crop_bottom': 0          # Pixels to crop from bottom
            }
        }
        
        # Display options
        self.display_options = {
            'show_info_overlay': True,   # Show text overlay with source info
            'fullscreen': True,          # Run in fullscreen mode
            'center_frame': True,        # Center the frame on screen (overrides position)
            'preserve_aspect_ratio': True, # Keep aspect ratio when scaling
            'target_fps': 30             # Target frame rate for display
        }
        
        # Load settings from config.ini
        self._load_config()
        
        # Set environment variable to ensure display on the physical screen
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
        
        # Debugging
        self.debug_mode = False  # Set to True for debug overlay
        
        print("Video display initialized with settings:")
        print(f"Display dimensions: {self.window_width}x{self.window_height}")
        print(f"Internal camera: rotation={self.camera_settings['internal']['rotation']}, "
              f"scale={self.camera_settings['internal']['scale']}")
        print(f"External camera: rotation={self.camera_settings['external']['rotation']}, "
              f"scale={self.camera_settings['external']['scale']}")
    
    def _load_config(self):
        """Load display settings from config.ini"""
        try:
            config = configparser.ConfigParser()
            config.read('config.ini')
            
            if 'video' in config:
                # Load display dimensions
                self.window_width = config.getint('video', 'display_width', fallback=1280)
                self.window_height = config.getint('video', 'display_height', fallback=800)
                
                # Load internal camera settings
                self._load_camera_config(config, 'internal')
                
                # Load external camera settings
                self._load_camera_config(config, 'external')
                
                # Load display options
                self._load_display_options(config)
                
                print(f"Loaded display settings from config.ini")
            else:
                print("No [video] section found in config.ini, using default settings")
                # Add default settings to config
                self._add_default_config_settings(config)
                
        except Exception as e:
            print(f"Error loading config: {e}")
            print("Using default settings")
    
    def _load_camera_config(self, config, camera_type):
        """Load configuration for a specific camera type (internal/external)"""
        prefix = f"{camera_type}_"
        
        # Load rotation setting
        if f'{prefix}rotation' in config['video']:
            self.camera_settings[camera_type]['rotation'] = config['video'][f'{prefix}rotation']
        
        # Load position settings
        if f'{prefix}position_x' in config['video']:
            self.camera_settings[camera_type]['position_x'] = config.getint('video', f'{prefix}position_x', fallback=0)
        if f'{prefix}position_y' in config['video']:
            self.camera_settings[camera_type]['position_y'] = config.getint('video', f'{prefix}position_y', fallback=0)
        
        # Load scaling factor
        if f'{prefix}scale' in config['video']:
            self.camera_settings[camera_type]['scale'] = config.getfloat('video', f'{prefix}scale', fallback=1.0)
        
        # Load cropping settings
        if f'{prefix}crop_left' in config['video']:
            self.camera_settings[camera_type]['crop_left'] = config.getint('video', f'{prefix}crop_left', fallback=0)
        if f'{prefix}crop_right' in config['video']:
            self.camera_settings[camera_type]['crop_right'] = config.getint('video', f'{prefix}crop_right', fallback=0)
        if f'{prefix}crop_top' in config['video']:
            self.camera_settings[camera_type]['crop_top'] = config.getint('video', f'{prefix}crop_top', fallback=0)
        if f'{prefix}crop_bottom' in config['video']:
            self.camera_settings[camera_type]['crop_bottom'] = config.getint('video', f'{prefix}crop_bottom', fallback=0)
    
    def _load_display_options(self, config):
        """Load display options from config"""
        if 'show_info_overlay' in config['video']:
            self.display_options['show_info_overlay'] = config.getboolean('video', 'show_info_overlay', fallback=True)
        if 'fullscreen' in config['video']:
            self.display_options['fullscreen'] = config.getboolean('video', 'fullscreen', fallback=True)
        if 'center_frame' in config['video']:
            self.display_options['center_frame'] = config.getboolean('video', 'center_frame', fallback=True)
        if 'preserve_aspect_ratio' in config['video']:
            self.display_options['preserve_aspect_ratio'] = config.getboolean('video', 'preserve_aspect_ratio', fallback=True)
        if 'target_fps' in config['video']:
            self.display_options['target_fps'] = config.getint('video', 'target_fps', fallback=30)
        if 'debug_mode' in config['video']:
            self.debug_mode = config.getboolean('video', 'debug_mode', fallback=False)
    
    def _add_default_config_settings(self, config):
        """Add default settings to config.ini if not present"""
        try:
            if 'video' not in config:
                config['video'] = {}
            
            # Display dimensions
            if 'display_width' not in config['video']:
                config['video']['display_width'] = str(self.window_width)
            if 'display_height' not in config['video']:
                config['video']['display_height'] = str(self.window_height)
            
            # Rotation settings with options comment
            config['video']['# Rotation options'] = '0, 90_clockwise, 90_counter, 180'
            
            # Add camera settings for internal and external cameras
            for camera_type in ['internal', 'external']:
                prefix = f"{camera_type}_"
                settings = self.camera_settings[camera_type]
                
                # Add each setting with a comment
                config['video'][f"# {camera_type.capitalize()} camera settings"] = ""
                config['video'][f"{prefix}rotation"] = settings['rotation']
                config['video'][f"{prefix}position_x"] = str(settings['position_x'])
                config['video'][f"{prefix}position_y"] = str(settings['position_y'])
                config['video'][f"{prefix}scale"] = str(settings['scale'])
                config['video'][f"{prefix}crop_left"] = str(settings['crop_left'])
                config['video'][f"{prefix}crop_right"] = str(settings['crop_right'])
                config['video'][f"{prefix}crop_top"] = str(settings['crop_top'])
                config['video'][f"{prefix}crop_bottom"] = str(settings['crop_bottom'])
            
            # Add display options
            config['video']['# Display options'] = ""
            config['video']['show_info_overlay'] = str(self.display_options['show_info_overlay'])
            config['video']['fullscreen'] = str(self.display_options['fullscreen'])
            config['video']['center_frame'] = str(self.display_options['center_frame'])
            config['video']['preserve_aspect_ratio'] = str(self.display_options['preserve_aspect_ratio'])
            config['video']['target_fps'] = str(self.display_options['target_fps'])
            config['video']['debug_mode'] = str(self.debug_mode)
            
            # Write to config file
            with open('config.ini', 'w') as configfile:
                config.write(configfile)
                
            print("Added default display settings to config.ini")
        except Exception as e:
            print(f"Error adding default settings to config: {e}")
    
    def start(self) -> bool:
        """Start the video display system."""
        print("Starting video display...")
        
        # Try to create window with proper fullscreen setup
        try:
            # First destroy any existing windows with the same name
            try:
                cv2.destroyWindow(self.window_name)
            except():
                pass  # Silently ignore if window doesn't exist
            
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
            
            # Set fullscreen if enabled
            if self.display_options['fullscreen']:
                cv2.setWindowProperty(self.window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
            
            # Try to set window to stay on top
            cv2.setWindowProperty(self.window_name, cv2.WND_PROP_TOPMOST, 1)
            
            # Wait a moment for window to settle
            time.sleep(0.5)
            
            print("Window created successfully")
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
    
    def _should_display_video(self) -> Tuple[bool, str, Optional[np.ndarray], str]:
        """
        Determine if video should be displayed based on pressure states.
        
        Returns:
            Tuple of (should_display, source_description, frame_to_display, camera_type)
        """
        local_state = system_state.get_local_state()
        remote_state = system_state.get_remote_state()
        
        # No pressure on either device: Display nothing (black screen)
        if not local_state.get("pressure", False) and not remote_state.get("pressure", False):
            return (False, "No Display (No Pressure)", None, "")
            
        # Local pressure: Display remote external camera video
        elif local_state.get("pressure", False) and not remote_state.get("pressure", False):
            return (True, "Remote External Camera", self.video_streamer.get_received_external_frame(), "external")
            
        # Remote pressure: Display nothing
        elif not local_state.get("pressure", False) and remote_state.get("pressure", False):
            return (False, "No Display (Remote Pressure)", None, "")
            
        # Both have pressure: Display remote internal camera video
        elif local_state.get("pressure", False) and remote_state.get("pressure", False):
            return (True, "Remote Internal Camera", self.video_streamer.get_received_internal_frame(), "internal")
            
        # Default case
        return (False, "No Display (Default)", None, "")
    
    def _on_internal_frame_update(self, frame: np.ndarray) -> None:
        """Handle new internal frame from remote device."""
        self.internal_frame_updated.set()
    
    def _on_external_frame_update(self, frame: np.ndarray) -> None:
        """Handle new external frame from remote device."""
        self.external_frame_updated.set()
        
    def _process_frame(self, frame: np.ndarray, camera_type: str) -> np.ndarray:
        """
        Process a frame with rotation, cropping, scaling and positioning.
        
        Args:
            frame: Input frame
            camera_type: 'internal' or 'external'
            
        Returns:
            Processed frame ready for display
        """
        if frame is None:
            return None
            
        # Get camera settings
        settings = self.camera_settings[camera_type]
        
        # Create a copy of the frame to avoid modifying the original
        processed = frame.copy()
        
        # Apply crop if specified
        h, w = processed.shape[:2]
        crop_left = min(settings['crop_left'], w-1)
        crop_right = min(settings['crop_right'], w-1)
        crop_top = min(settings['crop_top'], h-1)
        crop_bottom = min(settings['crop_bottom'], h-1)
        
        if crop_left > 0 or crop_right > 0 or crop_top > 0 or crop_bottom > 0:
            # Calculate new dimensions after cropping
            new_w = w - crop_left - crop_right
            new_h = h - crop_top - crop_bottom
            
            # Ensure we have valid dimensions
            if new_w > 0 and new_h > 0:
                processed = processed[crop_top:h-crop_bottom, crop_left:w-crop_right]
        
        # Apply rotation
        rotation_type = settings['rotation']
        if rotation_type == '90_clockwise':
            processed = cv2.rotate(processed, cv2.ROTATE_90_CLOCKWISE)
        elif rotation_type == '90_counter':
            processed = cv2.rotate(processed, cv2.ROTATE_90_COUNTERCLOCKWISE)
        elif rotation_type == '180':
            processed = cv2.rotate(processed, cv2.ROTATE_180)
        
        # Apply scaling
        if settings['scale'] != 1.0:
            h, w = processed.shape[:2]
            new_w = int(w * settings['scale'])
            new_h = int(h * settings['scale'])
            if new_w > 0 and new_h > 0:
                processed = cv2.resize(processed, (new_w, new_h))
        
        # Create background with display dimensions
        background = np.zeros((self.window_height, self.window_width, 3), dtype=np.uint8)
        
        # Calculate position for the frame
        h, w = processed.shape[:2]
        
        if self.display_options['center_frame']:
            # Center the frame on the display
            x_offset = (self.window_width - w) // 2
            y_offset = (self.window_height - h) // 2
        else:
            # Use configured position
            x_offset = settings['position_x']
            y_offset = settings['position_y']
        
        # Ensure offsets are within bounds
        x_offset = max(0, min(x_offset, self.window_width - 1))
        y_offset = max(0, min(y_offset, self.window_height - 1))
        
        # Calculate the region where the frame will be placed
        target_h = min(h, self.window_height - y_offset)
        target_w = min(w, self.window_width - x_offset)
        
        if target_h > 0 and target_w > 0:
            # Place the frame on the background
            background[y_offset:y_offset+target_h, x_offset:x_offset+target_w] = processed[:target_h, :target_w]
        
        # Add debug overlay
        if self.debug_mode:
            self._add_debug_overlay(background, camera_type, settings, (x_offset, y_offset, w, h))
        
        return background
    
    def _add_debug_overlay(self, frame, camera_type, settings, frame_info):
        """Add debug information overlay to the frame"""
        x_offset, y_offset, w, h = frame_info
        
        # Add camera type and settings
        cv2.putText(frame, f"Camera: {camera_type}", (10, 30),
                  cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1)
        cv2.putText(frame, f"Rotation: {settings['rotation']}", (10, 50),
                  cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1)
        cv2.putText(frame, f"Scale: {settings['scale']}", (10, 70),
                  cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1)
        cv2.putText(frame, f"Position: ({x_offset}, {y_offset})", (10, 90),
                  cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1)
        cv2.putText(frame, f"Frame size: {w}x{h}", (10, 110),
                  cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1)
        
        # Draw frame boundary box
        cv2.rectangle(frame, (x_offset, y_offset), (x_offset + w, y_offset + h), (0, 255, 0), 1)
    
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
            frame_time_total = 0
            
            # Create a black frame (for when nothing should be displayed)
            black_frame = np.zeros((self.window_height, self.window_width, 3), dtype=np.uint8)
            
            # Force the window to be shown initially with a black frame
            if display_available:
                cv2.imshow(self.window_name, black_frame)
                cv2.waitKey(1)
                
                # Make sure window is in fullscreen mode if enabled
                if self.display_options['fullscreen']:
                    cv2.setWindowProperty(self.window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
            
            last_state_debug = None
            
            while self.running:
                frame_start_time = time.time()
                
                # Calculate frame interval based on target FPS
                frame_interval = 1.0 / self.display_options['target_fps']
                
                # Limit update rate to target FPS
                if frame_start_time - last_render_time >= frame_interval:
                    # Determine what to display based on pressure states
                    should_display, source_desc, frame, camera_type = self._should_display_video()
                    
                    # Generate state description for debugging
                    local_state = system_state.get_local_state()
                    remote_state = system_state.get_remote_state()
                    state_debug = f"Local pressure: {local_state.get('pressure', False)}, Remote pressure: {remote_state.get('pressure', False)}"
                    
                    # Log state changes for debugging
                    if state_debug != last_state_debug:
                        print(f"Display state change: {state_debug} - Display mode: {source_desc}")
                        last_state_debug = state_debug
                    
                    if should_display and frame is not None:
                        # Process frame based on camera type
                        display_frame = self._process_frame(frame, camera_type)
                        
                        # Add source info if enabled
                        if self.display_options['show_info_overlay']:
                            cv2.putText(display_frame, source_desc, (10, 30),
                                      cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
                        
                        # Log frame information periodically
                        frame_counter += 1
                        if frame_counter % 100 == 0:
                            avg_frame_time = frame_time_total / 100
                            avg_fps = 1.0 / avg_frame_time if avg_frame_time > 0 else 0
                            print(f"Frame {frame_counter}: {source_desc} - Avg FPS: {avg_fps:.1f}")
                            frame_time_total = 0
                    else:
                        # Use a completely black frame
                        display_frame = black_frame.copy()
                        
                        # Add explanation text only if there's something to explain
                        if source_desc and self.display_options['show_info_overlay']:
                            cv2.putText(display_frame, source_desc, (10, 30),
                                      cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
                    
                    # Show display if available
                    if display_available:
                        cv2.imshow(self.window_name, display_frame)
                        cv2.waitKey(1)
                        
                        # Make sure fullscreen is maintained
                        if self.display_options['fullscreen'] and frame_counter % 30 == 0:
                            cv2.setWindowProperty(self.window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
                    
                    last_render_time = frame_start_time
                    
                    # Reset update flags
                    self.internal_frame_updated.clear()
                    self.external_frame_updated.clear()
                    
                    # Track frame time for FPS calculation
                    frame_time = time.time() - frame_start_time
                    frame_time_total += frame_time
                
                # Wait for frame updates or timeout - use a short timeout to maintain responsive frame rate
                wait_time = max(0.001, frame_interval - (time.time() - frame_start_time))
                self.internal_frame_updated.wait(timeout=wait_time)
                self.external_frame_updated.wait(timeout=0.001)
        
        except Exception as e:
            print(f"Error in display loop: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if display_available:
                cv2.destroyAllWindows()
            print("Display loop stopped")
