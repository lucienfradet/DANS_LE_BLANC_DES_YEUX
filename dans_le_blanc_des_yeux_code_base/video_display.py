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
    """Handles displaying video streams on the physical display with enhanced controls and idle mode awareness."""
    
    def __init__(self, video_streamer: VideoStreamer, camera_manager: CameraManager):
        self.video_streamer = video_streamer
        self.camera_manager = camera_manager
        
        # Display parameters for Waveshare 7-inch display (default values)
        self.window_width = 1024
        self.window_height = 600
        
        # Default camera settings (truncated for brevity)
        self.camera_settings = {
            'internal': {
                'rotation': '0',
                'position_x': 0,
                'position_y': 0,
                'scale': 1.0,
                'crop_left': 0,
                'crop_right': 0,
                'crop_top': 0,
                'crop_bottom': 0
            },
            'external': {
                'rotation': '0',
                'position_x': 0,
                'position_y': 0,
                'scale': 1.0,
                'crop_left': 0,
                'crop_right': 0,
                'crop_top': 0,
                'crop_bottom': 0
            }
        }
        
        # Display options
        self.display_options = {
            'show_info_overlay': False,
            'fullscreen': True,
            'center_frame': False,
            'preserve_aspect_ratio': True,
            'target_fps': 30
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
        self.debug_mode = False
        
        # Frame counting and logging
        self.frame_counter = 0
        self.frame_time_total = 0
        self.log_interval = 300  # Log display stats every 5 minutes
        self.last_log_time = time.time()
        
        print("Video display initialized with optimized logging")
    
    def _load_config(self):
        """Load display settings from config.ini (implementation omitted for brevity)"""
        # Original implementation...
        pass
    
    def _on_state_change(self, changed_state: str) -> None:
        """Handle system state changes."""
        if changed_state in ["local", "remote"]:
            # Force a display update when pressure states change
            self.internal_frame_updated.set()
            self.external_frame_updated.set()
        elif changed_state == "idle_mode":
            # Update logging interval based on idle mode
            if system_state.is_idle_mode():
                # When idle, log much less frequently
                self.log_interval = 3600  # Once per hour in idle mode
            else:
                # In active mode, log more frequently
                self.log_interval = 300  # Every 5 minutes in active mode
    
    def _should_log_stats(self) -> bool:
        """Determine if it's time to log display statistics based on time interval."""
        current_time = time.time()
        if current_time - self.last_log_time >= self.log_interval:
            self.last_log_time = current_time
            return True
        return False
    
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
        
        print(f"Video display stopped. Total frames displayed: {self.frame_counter}")
    
    def _on_internal_frame_update(self, frame: np.ndarray) -> None:
        """Handle new internal frame from remote device."""
        self.internal_frame_updated.set()
    
    def _on_external_frame_update(self, frame: np.ndarray) -> None:
        """Handle new external frame from remote device."""
        self.external_frame_updated.set()
        
    def _display_loop(self) -> None:
        """Main display loop with idle mode awareness for logging optimization."""
        print("Display loop started with adaptive logging")
        
        # Check if display is available
        display_available = "DISPLAY" in os.environ
        if not display_available:
            print("Warning: DISPLAY environment variable not set. Running in headless mode.")
        
        try:
            last_render_time = 0
            
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
                        if self.display_options['fullscreen'] and self.frame_counter % 30 == 0:
                            cv2.setWindowProperty(self.window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
                    
                    last_render_time = frame_start_time
                    
                    # Reset update flags
                    self.internal_frame_updated.clear()
                    self.external_frame_updated.clear()
                    
                    # Track frame time for FPS calculation
                    frame_time = time.time() - frame_start_time
                    self.frame_time_total += frame_time
                    self.frame_counter += 1
                    
                    # Log frame information periodically based on time interval rather than frame count
                    if self._should_log_stats():
                        avg_frame_time = self.frame_time_total / max(1, self.frame_counter - self.frame_counter % 100)
                        avg_fps = 1.0 / avg_frame_time if avg_frame_time > 0 else 0
                        
                        is_idle = system_state.is_idle_mode()
                        status = "IDLE" if is_idle else "ACTIVE"
                        
                        print(f"Display status: {status} - {source_desc} - Frames: {self.frame_counter}, Avg FPS: {avg_fps:.1f}")
                        
                        # Reset frame time total for next period
                        self.frame_time_total = 0
                
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

    def _process_frame(self, frame: np.ndarray, camera_type: str) -> np.ndarray:
        """Process a frame with rotation, cropping, scaling and positioning."""
        # Implementation unchanged - truncated for brevity
        pass
            
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
