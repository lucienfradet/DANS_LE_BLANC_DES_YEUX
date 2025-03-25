import cv2
import numpy as np
import threading
import time
import gi
from enum import Enum

gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
from gi.repository import Gst, GObject, GstApp

from shared_variables import local_osc, received_osc
from streamer import get_video_streamer
from eye_detector import get_eye_detector

# Define display states
class DisplayState(Enum):
    HOME = 1
    REMOTE_FEED = 2
    SOMEONE_COMING = 3
    EYE_DETECTION = 4

class Display:
    def __init__(self, window_name="Dans le Blanc des Yeux", fullscreen=False):
        self.window_name = window_name
        self.fullscreen = fullscreen
        self.running = False
        self.thread = None
        self.current_state = DisplayState.HOME
        self.transition_active = False
        self.fade_alpha = 0.0  # For transitions
        
        # Frame buffers
        self.home_frame = None
        self.remote_frame = None
        self.overlay_frame = None
        self.display_frame = None
        self.frame_lock = threading.Lock()
        
        # Animation properties
        self.pulsate_alpha = 0.0
        self.pulsate_increasing = True
        
        # Load video streamer and eye detector
        self.video_streamer = get_video_streamer()
        self.eye_detector = get_eye_detector()
        
        # Set up camera for home feed
        self.setup_home_camera()
        
    def setup_home_camera(self):
        """Initialize camera for home feed"""
        self.home_cap = cv2.VideoCapture(0)  # Use default camera
        
        # Check if camera opened successfully
        if not self.home_cap.isOpened():
            print("Error: Could not open home camera.")
            # Use a black frame as fallback
            self.home_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    
    def start(self):
        """Start display in a separate thread"""
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._display_loop)
            self.thread.daemon = True
            self.thread.start()
            print("Display started")
    
    def stop(self):
        """Stop display"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)
        if self.home_cap and self.home_cap.isOpened():
            self.home_cap.release()
        cv2.destroyAllWindows()
        print("Display stopped")
    
    def _display_loop(self):
        """Main display loop running in thread"""
        # Create window
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
        if self.fullscreen:
            cv2.setWindowProperty(self.window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        
        last_time = time.time()
        
        while self.running:
            current_time = time.time()
            delta_time = current_time - last_time
            last_time = current_time
            
            # Update the animation timing
            self._update_animations(delta_time)
            
            # Get frames from sources
            self._update_frames()
            
            # Determine the current state based on shared variables
            self._update_state()
            
            # Render the frame based on current state
            self._render_frame()
            
            # Display the frame
            with self.frame_lock:
                if self.display_frame is not None:
                    cv2.imshow(self.window_name, self.display_frame)
            
            # Exit on 'q' key
            if cv2.waitKey(1) & 0xFF == ord('q'):
                self.running = False
                break
            
            # Process at reasonable frame rate
            time.sleep(0.033)  # ~30 FPS
    
    def _update_animations(self, delta_time):
        """Update animation parameters"""
        # Update pulsating effect
        if self.pulsate_increasing:
            self.pulsate_alpha += delta_time * 0.5  # Adjust speed as needed
            if self.pulsate_alpha >= 1.0:
                self.pulsate_alpha = 1.0
                self.pulsate_increasing = False
        else:
            self.pulsate_alpha -= delta_time * 0.5
            if self.pulsate_alpha <= 0.3:  # Don't go completely transparent
                self.pulsate_alpha = 0.3
                self.pulsate_increasing = True
        
        # Update transitions
        if self.transition_active:
            self.fade_alpha += delta_time
            if self.fade_alpha >= 1.0:
                self.fade_alpha = 1.0
                self.transition_active = False
    
    def _update_frames(self):
        """Update frames from various sources"""
        # Update home frame
        ret, frame = self.home_cap.read()
        if ret:
            with self.frame_lock:
                self.home_frame = frame
        
        # Update remote frame from video_streamer if available
        # In a real implementation, you would get this from GStreamer
        # For now, we'll use a placeholder
        with self.frame_lock:
            # Placeholder - in reality this would come from streamer.py
            if self.remote_frame is None:
                self.remote_frame = np.zeros_like(self.home_frame)
                font = cv2.FONT_HERSHEY_SIMPLEX
                cv2.putText(self.remote_frame, 'Remote Feed Placeholder', 
                           (50, 240), font, 1, (255, 255, 255), 2)
        
        # Update eye detection overlay if in appropriate state
        if self.current_state == DisplayState.EYE_DETECTION:
            ir_slit = self.eye_detector.get_ir_slit_frame()
            if ir_slit is not None:
                with self.frame_lock:
                    self.overlay_frame = ir_slit
    
    def _update_state(self):
        """Update display state based on shared variables"""
        previous_state = self.current_state
        
        # State transitions based on pressure plates and eye detection
        if local_osc.get("pressure", False) and not received_osc.get("pressure", True):
            # Someone on our pressure plate, request remote feed
            if self.current_state != DisplayState.REMOTE_FEED:
                self.current_state = DisplayState.REMOTE_FEED
                self.video_streamer.start_receiving()
        
        elif received_osc.get("pressure", True) and not local_osc.get("pressure", False):
            # Someone on remote pressure plate
            self.current_state = DisplayState.SOMEONE_COMING
            if not self.video_streamer.streaming:
                self.video_streamer.start_streaming()
        
        elif local_osc.get("eyes_detected", False) and received_osc.get("eyes_detected", False):
            # Eyes detected on both sides
            self.current_state = DisplayState.EYE_DETECTION
        
        else:
            # Default state - home
            if received_osc.get("pressure", True) and local_osc.get("pressure", False):
                self.current_state = DisplayState.HOME
                self.video_streamer.stop_streaming()
                self.video_streamer.stop_receiving()
        
        # Check if state has changed
        if previous_state != self.current_state:
            print(f"Display state changed: {previous_state} -> {self.current_state}")
            self.transition_active = True
            self.fade_alpha = 0.0
    
    def _render_frame(self):
        """Render the display frame based on current state"""
        with self.frame_lock:
            if self.home_frame is None:
                return
            
            # Base frame depends on state
            if self.current_state == DisplayState.HOME:
                base_frame = self.home_frame.copy()
            elif self.current_state == DisplayState.REMOTE_FEED:
                base_frame = self.remote_frame.copy() if self.remote_frame is not None else self.home_frame.copy()
            elif self.current_state == DisplayState.SOMEONE_COMING:
                base_frame = self.home_frame.copy()
                # Add pulsating "QUELQU'UN ARRIVE..." text
                overlay = base_frame.copy()
                font = cv2.FONT_HERSHEY_SIMPLEX
                text = "QUELQU'UN ARRIVE..."
                text_size = cv2.getTextSize(text, font, 1.5, 2)[0]
                text_x = (base_frame.shape[1] - text_size[0]) // 2
                text_y = (base_frame.shape[0] + text_size[1]) // 2
                cv2.putText(overlay, text, (text_x, text_y), font, 1.5, (0, 0, 255), 2)
                
                # Apply pulsating alpha
                cv2.addWeighted(overlay, self.pulsate_alpha, base_frame, 1 - self.pulsate_alpha, 0, base_frame)
            elif self.current_state == DisplayState.EYE_DETECTION:
                base_frame = self.home_frame.copy()
                # Overlay IR eye slit if available
                if self.overlay_frame is not None:
                    # Resize overlay to fit in the middle of the frame
                    h, w = self.overlay_frame.shape[:2]
                    y_offset = (base_frame.shape[0] - h) // 2
                    x_offset = (base_frame.shape[1] - w) // 2
                    
                    # Create a mask for overlay (non-black pixels)
                    gray = cv2.cvtColor(self.overlay_frame, cv2.COLOR_BGR2GRAY)
                    _, mask = cv2.threshold(gray, 10, 255, cv2.THRESH_BINARY)
                    
                    # Apply overlay only where mask is non-zero
                    roi = base_frame[y_offset:y_offset+h, x_offset:x_offset+w]
                    for c in range(3):  # For each color channel
                        roi[:, :, c] = np.where(mask > 0, 
                                              self.overlay_frame[:, :, c], 
                                              roi[:, :, c])
            
            # Handle transitions between states
            if self.transition_active and self.fade_alpha < 1.0:
                # Create fade effect
                black_frame = np.zeros_like(base_frame)
                fade_frame = cv2.addWeighted(
                    base_frame, 1.0 - self.fade_alpha,
                    black_frame, self.fade_alpha,
                    0
                )
                self.display_frame = fade_frame
            else:
                self.display_frame = base_frame

# Singleton instance
display = None

def get_display():
    """Get or create the Display instance"""
    global display
    if display is None:
        display = Display()
    return display

if __name__ == "__main__":
    # Test the display
    disp = get_display()
    eye_detector = get_eye_detector()
    
    # Start components
    eye_detector.start()
    disp.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        disp.stop()
        eye_detector.stop()
