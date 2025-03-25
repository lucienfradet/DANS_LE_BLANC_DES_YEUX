"""
Main controller for the 'Dans le Blanc des Yeux' art installation.
This coordinates all components and manages the overall application flow.
"""

import threading
import time
import signal
import sys

# Import components
from shared_variables import get_system_state
from display import get_display
from streamer import get_video_streamer
from eye_detector import get_eye_detector
from osc_handler import run_osc_handler
from shared_variables import local_osc, received_osc

class Controller:
    def __init__(self):
        # Get component instances
        self.system_state = get_system_state()
        self.display = get_display()
        self.video_streamer = get_video_streamer()
        self.eye_detector = get_eye_detector()
        
        # Control flags
        self.running = False
        self.main_thread = None
    
    def start(self):
        """Start all components and main control loop"""
        if self.running:
            return
        
        self.running = True
        
        # Register for state change notifications
        self.system_state.register_state_change_callback(self.on_state_change)
        
        # Start components
        self.eye_detector.start()
        self.display.start()
        
        # Start main control thread
        self.main_thread = threading.Thread(target=self._main_loop)
        self.main_thread.daemon = True
        self.main_thread.start()
        
        print("Controller started")
    
    def stop(self):
        """Stop all components and clean up"""
        if not self.running:
            return
        
        self.running = False
        
        # Stop components
        self.eye_detector.stop()
        self.display.stop()
        if self.video_streamer:
            self.video_streamer.cleanup()
        
        # Wait for main thread to finish
        if self.main_thread:
            self.main_thread.join(timeout=1.0)
        
        # Unregister callbacks
        self.system_state.unregister_state_change_callback(self.on_state_change)
        
        print("Controller stopped")
    
    def on_state_change(self, old_state, new_state):
        """Handle system state changes"""
        print(f"System state changed: {old_state} -> {new_state}")
        
        # React to state transitions
        if new_state == "REMOTE_FEED":
            # We need to see remote feed
            self.video_streamer.start_receiving()
        
        elif new_state == "SOMEONE_COMING":
            # Someone at the other device - need to stream
            self.video_streamer.start_streaming()
        
        elif new_state == "EYE_DETECTION":
            # Both sides have eye detection - ensure streaming
            if not self.video_streamer.streaming:
                self.video_streamer.start_streaming()
        
        elif new_state == "HOME":
            # Default state - stop streaming/receiving
            self.video_streamer.stop_streaming()
            self.video_streamer.stop_receiving()
    
    def _main_loop(self):
        """Main control loop"""
        while self.running:
            # Check conditions and update system state
            self._update_system_state()
            
            # Small delay to avoid high CPU usage
            time.sleep(0.1)
    
    def _update_system_state(self):
        """Update system state based on sensor data"""
        current_state = self.system_state.get_state()
        
        # Check conditions for state transitions based on pressure plates and eye detection
        if local_osc.get("pressure", False) and not received_osc.get("pressure", True):
            # Someone on our pressure plate, request remote feed
            if current_state != "REMOTE_FEED":
                self.system_state.set_state("REMOTE_FEED")
        
        elif received_osc.get("pressure", True) and not local_osc.get("pressure", False):
            # Someone on remote pressure plate
            if current_state != "SOMEONE_COMING":
                self.system_state.set_state("SOMEONE_COMING")
        
        elif local_osc.get("eyes_detected", False) and received_osc.get("eyes_detected", False):
            # Eyes detected on both sides
            if current_state != "EYE_DETECTION":
                self.system_state.set_state("EYE_DETECTION")
        
        elif received_osc.get("pressure", True) and local_osc.get("pressure", False):
            # No one on either pressure plate
            if current_state != "HOME":
                self.system_state.set_state("HOME")

# Main function to run the application
def run_controller():
    controller = Controller()
    
    # Set up signal handlers for graceful shutdown
    def signal_handler(sig, frame):
        print("Shutting down...")
        controller.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start the controller
    controller.start()
    
    # Start OSC handler in main thread
    try:
        run_osc_handler()
    except KeyboardInterrupt:
        controller.stop()

if __name__ == "__main__":
    run_controller()
