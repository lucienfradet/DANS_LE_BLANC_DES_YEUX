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
        
        # Display parameters
        self.window_width = 1280
        self.window_height = 720
        
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
        
        # Try to create window
        try:
            cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(self.window_name, self.window_width, self.window_height)
            
            # Try to set window to fullscreen
            cv2.setWindowProperty(self.window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
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
        
        # Case 1: Local has pressure, display remote external camera
        if local_state.get("pressure", False):
            if remote_state.get("pressure", False):
                # Both have pressure: show remote internal camera
                return (True, "Remote Internal Camera", self.video_streamer.get_received_internal_frame())
            else:
                # Only local has pressure: show remote external camera
                return (True, "Remote External Camera", self.video_streamer.get_received_external_frame())
        
        # Case 2: No local pressure, don't show anything
        return (False, "No Display (No Local Pressure)", None)
    
    def _on_internal_frame_update(self, frame: np.ndarray) -> None:
        """Handle new internal frame from remote device."""
        self.internal_frame_updated.set()
    
    def _on_external_frame_update(self, frame: np.ndarray) -> None:
        """Handle new external frame from remote device."""
        self.external_frame_updated.set()
    
    def _display_loop(self) -> None:
        """Main display loop."""
        print("Display loop started")
        
        # Check if display is available
        display_available = "DISPLAY" in os.environ
        if not display_available:
            print("Warning: DISPLAY environment variable not set. Running in headless mode.")
        
        try:
            last_render_time = 0
            
            # Create a black frame (for when nothing should be displayed)
            black_frame = np.zeros((self.window_height, self.window_width, 3), dtype=np.uint8)
            
            while self.running:
                current_time = time.time()
                
                # Limit update rate to 30fps
                if current_time - last_render_time >= 0.033:  # ~30fps
                    # Determine what to display based on pressure states
                    should_display, source_desc, frame = self._should_display_video()
                    
                    if should_display and frame is not None:
                        # Resize the frame to fit our window
                        display_frame = cv2.resize(frame, (self.window_width, self.window_height))
                        
                        # Add source label to the top-left corner
                        cv2.putText(display_frame, source_desc, (10, 30), 
                                   cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
                    else:
                        # If nothing should be displayed or no frame is available, show black
                        display_frame = black_frame.copy()
                        
                        # Add explanation text if relevant
                        if source_desc:
                            cv2.putText(display_frame, source_desc, (10, 30), 
                                       cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
                    
                    # Show display if available
                    if display_available:
                        cv2.imshow(self.window_name, display_frame)
                        cv2.waitKey(1)
                    
                    last_render_time = current_time
                    
                    # Reset update flags
                    self.internal_frame_updated.clear()
                    self.external_frame_updated.clear()
                
                # Wait for frame updates or timeout
                self.internal_frame_updated.wait(timeout=0.01)
                self.external_frame_updated.wait(timeout=0.01)
        
        except Exception as e:
            print(f"Error in display loop: {e}")
        finally:
            if display_available:
                cv2.destroyAllWindows()
            print("Display loop stopped")


# Test function to run the video display standalone
def test_video_display():
    """Test the video display with camera manager and video streamer."""
    from camera_manager import CameraManager
    from video_streamer import VideoStreamer
    
    # Initialize system state with no pressure
    system_state.update_local_state({"pressure": False})
    system_state.update_remote_state({"pressure": False, "connected": True})
    
    # Initialize camera manager
    camera_manager = CameraManager()
    if not camera_manager.start():
        print("Failed to start camera manager")
        return
    
    # Initialize video streamer with loopback address for testing
    video_streamer = VideoStreamer(camera_manager, "127.0.0.1")
    video_streamer.start()
    
    # Initialize video display
    video_display = VideoDisplay(video_streamer, camera_manager)
    video_display.start()
    
    try:
        # Test different pressure states
        print("\nTesting default state (no pressure)...")
        time.sleep(3)
        
        print("\nTesting local pressure (should show remote external camera)...")
        system_state.update_local_state({"pressure": True})
        time.sleep(3)
        
        print("\nTesting remote pressure (should show nothing)...")
        system_state.update_local_state({"pressure": False})
        system_state.update_remote_state({"pressure": True})
        time.sleep(3)
        
        print("\nTesting both have pressure (should show remote internal camera)...")
        system_state.update_local_state({"pressure": True})
        time.sleep(3)
        
        # Return to normal state
        system_state.update_local_state({"pressure": False})
        system_state.update_remote_state({"pressure": False})
        
        print("\nTest complete. Press Ctrl+C to exit.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Test interrupted by user")
    finally:
        # Clean up
        video_display.stop()
        video_streamer.stop()
        camera_manager.stop()


# Run test if executed directly
if __name__ == "__main__":
    test_video_display()
