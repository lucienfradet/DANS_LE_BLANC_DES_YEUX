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
            rotation_type: Type of rotation ('90_clockwise', '180', etc.)
            is_external: Whether this is the external camera feed
            
        Returns:
            Processed frame that fits the display without stretching
        """
        if frame is None:
            return None
            
        # Apply the appropriate rotation
        if rotation_type == '90_clockwise':
            # 90 degrees clockwise rotation for external camera
            rotated = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif rotation_type == '90_counter':
            rotated = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        elif rotation_type == '180':
            # 180 degrees rotation for internal camera
            rotated = cv2.rotate(frame, cv2.ROTATE_180)
        else:
            rotated = frame.copy()
            
        # # Create a black background image with the size of the display
        # background = np.zeros((self.window_height, self.window_width, 3), dtype=np.uint8)
        # 
        # # Calculate the aspect ratio of the rotated frame
        # h, w = rotated.shape[:2]
        # aspect_ratio = w / h
        # 
        # # Calculate dimensions to maintain aspect ratio
        # if (self.window_width / self.window_height) > aspect_ratio:
        #     # Window is wider than the frame's aspect ratio
        #     new_height = self.window_height
        #     new_width = int(new_height * aspect_ratio)
        # else:
        #     # Window is taller than the frame's aspect ratio
        #     new_width = self.window_width
        #     new_height = int(new_width / aspect_ratio)
        #     
        # # Resize the frame while maintaining aspect ratio
        # resized = cv2.resize(rotated, (new_width, new_height))
        # 
        # # Calculate position to center the frame on the background
        # y_offset = (self.window_height - new_height) // 2
        # x_offset = (self.window_width - new_width) // 2
        # 
        # # Place the resized frame on the black background
        # background[y_offset:y_offset+new_height, x_offset:x_offset+new_width] = resized
        # 
        # return background
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
                        
                        # Apply rotation and aspect ratio preservation based on camera type
                        if "External" in source_desc:
                            # External camera - 90 degrees clockwise rotation
                            display_frame = self._rotate_and_fit_frame(frame, '90_counter', is_external=True)
                        else:
                            # Internal camera - 180 degrees rotation
                            display_frame = self._rotate_and_fit_frame(frame, '0', is_external=False)
                        
                        # Add source label to the top-left corner
                        cv2.putText(display_frame, source_desc, (10, 30), 
                                  cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
                    else:
                        # Use a completely black frame
                        display_frame = black_frame.copy()
                        
                        # Add explanation text only if there's something to explain
                        if source_desc:
                            cv2.putText(display_frame, source_desc, (10, 30), 
                                      cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
                    
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
