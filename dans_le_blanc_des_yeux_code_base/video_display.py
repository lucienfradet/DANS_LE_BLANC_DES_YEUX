"""
Video display module for the Dans le Blanc des Yeux installation.
Handles displaying video streams on the physical display.
"""

import os
import time
import threading
import subprocess
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
        self.display_layout = "grid"  # 'grid', 'single', 'picture-in-picture'
        self.active_display = 0  # For single view: 0=local internal, 1=local external, 2=remote internal, 3=remote external
        
        # Set environment variable to ensure display on the physical screen
        # This helps when running over SSH
        os.environ["DISPLAY"] = ":0"
        
        # Threading
        self.running = False
        self.thread = None
        
        # Window names
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
    
    def set_display_layout(self, layout: str) -> None:
        """Set the display layout."""
        if layout in ["grid", "single", "picture-in-picture"]:
            self.display_layout = layout
            print(f"Display layout set to: {layout}")
        else:
            print(f"Invalid layout: {layout}")
    
    def set_active_display(self, index: int) -> None:
        """Set the active display for single view."""
        if 0 <= index <= 3:
            self.active_display = index
            if self.display_layout == "single":
                print(f"Active display set to: {index}")
        else:
            print(f"Invalid display index: {index}")
    
    def _on_state_change(self, changed_state: str) -> None:
        """Handle system state changes."""
        if changed_state in ["local", "remote"]:
            self._handle_pressure_state_change()
    
    def _handle_pressure_state_change(self) -> None:
        """Handle changes in pressure state to control display layout."""
        local_state = system_state.get_local_state()
        remote_state = system_state.get_remote_state()
        
        # If local has pressure, show the remote external camera
        if local_state["pressure"] and remote_state["connected"]:
            self.set_display_layout("single")
            self.set_active_display(3)  # Remote external camera
        
        # If remote has pressure, show the local external camera
        elif remote_state["pressure"] and remote_state["connected"]:
            self.set_display_layout("single")
            self.set_active_display(1)  # Local external camera
        
        # If no pressure on either device, show all cameras in grid layout
        else:
            self.set_display_layout("grid")
    
    def _on_internal_frame_update(self, frame: np.ndarray) -> None:
        """Handle new internal frame from remote device."""
        self.internal_frame_updated.set()
    
    def _on_external_frame_update(self, frame: np.ndarray) -> None:
        """Handle new external frame from remote device."""
        self.external_frame_updated.set()
    
    def _display_loop(self) -> None:
        """Main display loop."""
        print("Display loop started")
        
        # Check if display is available directly
        display_available = "DISPLAY" in os.environ
        if not display_available:
            print("Warning: DISPLAY environment variable not set. Running in headless mode.")
        
        try:
            last_render_time = 0
            while self.running:
                current_time = time.time()
                
                # Limit update rate to 30fps
                if current_time - last_render_time >= 0.033:  # ~30fps
                    # Get all frames
                    local_internal_frame = self.camera_manager.get_internal_frame()
                    local_external_frame = self.camera_manager.get_external_frame()
                    remote_internal_frame = self.video_streamer.get_received_internal_frame()
                    remote_external_frame = self.video_streamer.get_received_external_frame()
                    
                    # Create display based on layout
                    display_frame = self._create_display_frame(
                        local_internal_frame,
                        local_external_frame,
                        remote_internal_frame,
                        remote_external_frame
                    )
                    
                    # Show display
                    if display_frame is not None and display_available:
                        cv2.imshow(self.window_name, display_frame)
                        cv2.waitKey(1)
                    
                    last_render_time = current_time
                    
                    # Reset update flags
                    self.internal_frame_updated.clear()
                    self.external_frame_updated.clear()
                
                # Wait for frame updates or timeout
                if not self.internal_frame_updated.wait(timeout=0.01) and not self.external_frame_updated.wait(timeout=0.01):
                    # No updates, short sleep
                    time.sleep(0.01)
        
        except Exception as e:
            print(f"Error in display loop: {e}")
        finally:
            if display_available:
                cv2.destroyAllWindows()
            print("Display loop stopped")
    
    def _create_display_frame(self, local_internal: Optional[np.ndarray], 
                             local_external: Optional[np.ndarray],
                             remote_internal: Optional[np.ndarray],
                             remote_external: Optional[np.ndarray]) -> Optional[np.ndarray]:
        """Create a display frame based on the current layout."""
        if self.display_layout == "grid":
            return self._create_grid_layout(local_internal, local_external, remote_internal, remote_external)
        elif self.display_layout == "single":
            return self._create_single_layout(local_internal, local_external, remote_internal, remote_external)
        elif self.display_layout == "picture-in-picture":
            return self._create_pip_layout(local_internal, local_external, remote_internal, remote_external)
        else:
            return None
    
    def _create_grid_layout(self, local_internal: Optional[np.ndarray], 
                           local_external: Optional[np.ndarray],
                           remote_internal: Optional[np.ndarray],
                           remote_external: Optional[np.ndarray]) -> Optional[np.ndarray]:
        """Create a 2x2 grid layout with all cameras."""
        # Create blank frames for any missing camera
        blank_frame = np.zeros((self.window_height // 2, self.window_width // 2, 3), dtype=np.uint8)
        
        # Resize frames to fit grid
        frames = []
        for frame in [local_internal, local_external, remote_internal, remote_external]:
            if frame is None:
                frames.append(blank_frame.copy())
            else:
                frames.append(cv2.resize(frame, (self.window_width // 2, self.window_height // 2)))
        
        # Add labels to each frame
        labels = ["Local Internal", "Local External", "Remote Internal", "Remote External"]
        for i, frame in enumerate(frames):
            cv2.putText(frame, labels[i], (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        
        # Combine frames into grid
        top_row = np.hstack((frames[0], frames[1]))
        bottom_row = np.hstack((frames[2], frames[3]))
        grid = np.vstack((top_row, bottom_row))
        
        return grid
    
    def _create_single_layout(self, local_internal: Optional[np.ndarray], 
                             local_external: Optional[np.ndarray],
                             remote_internal: Optional[np.ndarray],
                             remote_external: Optional[np.ndarray]) -> Optional[np.ndarray]:
        """Create a single-camera layout based on active_display."""
        frames = [local_internal, local_external, remote_internal, remote_external]
        labels = ["Local Internal", "Local External", "Remote Internal", "Remote External"]
        
        frame = frames[self.active_display]
        
        if frame is None:
            # Create blank frame if camera is not available
            frame = np.zeros((self.window_height, self.window_width, 3), dtype=np.uint8)
            label_text = f"{labels[self.active_display]} (Not Available)"
        else:
            frame = cv2.resize(frame, (self.window_width, self.window_height))
            label_text = labels[self.active_display]
        
        # Add label
        cv2.putText(frame, label_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        
        return frame
    
    def _create_pip_layout(self, local_internal: Optional[np.ndarray], 
                          local_external: Optional[np.ndarray],
                          remote_internal: Optional[np.ndarray],
                          remote_external: Optional[np.ndarray]) -> Optional[np.ndarray]:
        """Create a picture-in-picture layout."""
        # Determine main and pip frames based on state
        local_state = system_state.get_local_state()
        remote_state = system_state.get_remote_state()
        
        # Default: Local internal as main, remote internal as PIP
        main_frame = local_internal
        pip_frame = remote_internal
        main_label = "Local Internal"
        pip_label = "Remote Internal"
        
        # If local has pressure, show remote external as main
        if local_state.get("pressure", False):
            main_frame = remote_external
            pip_frame = local_internal
            main_label = "Remote External"
            pip_label = "Local Internal"
        
        # If remote has pressure, show local external as main
        elif remote_state.get("pressure", False):
            main_frame = local_external
            pip_frame = remote_internal
            main_label = "Local External"
            pip_label = "Remote Internal"
        
        # Create base frame
        if main_frame is None:
            base = np.zeros((self.window_height, self.window_width, 3), dtype=np.uint8)
            main_label += " (Not Available)"
        else:
            base = cv2.resize(main_frame, (self.window_width, self.window_height))
        
        # Add main label
        cv2.putText(base, main_label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        
        # Add PIP if available
        if pip_frame is not None:
            # Size for PIP (1/4 of the screen width)
            pip_width = self.window_width // 4
            pip_height = self.window_height // 4
            
            # Resize PIP frame
            pip_resized = cv2.resize(pip_frame, (pip_width, pip_height))
            
            # Position in bottom-right corner
            x_offset = self.window_width - pip_width - 10
            y_offset = self.window_height - pip_height - 10
            
            # Create ROI
            roi = base[y_offset:y_offset+pip_height, x_offset:x_offset+pip_width]
            
            # Add border to PIP
            cv2.rectangle(base, (x_offset-2, y_offset-2), (x_offset+pip_width+2, y_offset+pip_height+2), (255, 255, 255), 2)
            
            # Add PIP to the main frame with some transparency
            alpha = 0.7
            cv2.addWeighted(pip_resized, alpha, roi, 1-alpha, 0, roi)
            
            # Add PIP label
            cv2.putText(base, pip_label, (x_offset + 5, y_offset + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        
        return base


# Test function to run the video display standalone
def test_video_display():
    """Test the video display with camera manager and video streamer."""
    from camera_manager import CameraManager
    from video_streamer import VideoStreamer
    
    # Initialize system state
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
        # Test different layouts
        print("Testing grid layout...")
        video_display.set_display_layout("grid")
        time.sleep(5)
        
        print("Testing single layout...")
        video_display.set_display_layout("single")
        for i in range(4):
            print(f"Testing single display {i}...")
            video_display.set_active_display(i)
            time.sleep(3)
        
        print("Testing picture-in-picture layout...")
        video_display.set_display_layout("picture-in-picture")
        time.sleep(5)
        
        # Test pressure changes
        print("Testing local pressure...")
        system_state.update_local_state({"pressure": True})
        time.sleep(5)
        
        print("Testing remote pressure...")
        system_state.update_local_state({"pressure": False})
        system_state.update_remote_state({"pressure": True})
        time.sleep(5)
        
        # Return to normal state
        system_state.update_remote_state({"pressure": False})
        video_display.set_display_layout("grid")
        
        print("Test complete. Press Ctrl+C to exit.")
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
