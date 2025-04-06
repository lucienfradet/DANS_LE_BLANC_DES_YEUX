# test_instance.py
import os
import time
import threading
from system_state import system_state
from audio_streamer import AudioStreamer
from audio_playback import AudioPlayback

# Define whether this is instance 1 or 2
INSTANCE = 1  # Change to 2 for the second instance
REMOTE_IP = "127.0.0.1"

# Create a basic system_state if it doesn't exist already
if not hasattr(system_state, 'get_local_state'):
    # Minimal implementation
    class SystemState:
        def __init__(self):
            self.local_state = {"pressure": False}
            self.remote_state = {"pressure": False, "connected": True}
            self.observers = []
        
        def get_local_state(self):
            return self.local_state
            
        def get_remote_state(self):
            return self.remote_state
            
        def update_local_state(self, state):
            self.local_state.update(state)
            for observer in self.observers:
                observer("local")
                
        def update_remote_state(self, state):
            self.remote_state.update(state)
            for observer in self.observers:
                observer("remote")
                
        def add_observer(self, observer):
            self.observers.append(observer)
            
        def update_audio_state(self, state):
            pass  # Not needed for testing
            
    system_state = SystemState()

# Initialize audio components
print(f"Starting instance {INSTANCE}")
audio_streamer = AudioStreamer(REMOTE_IP)
audio_playback = AudioPlayback(audio_streamer)

# Start audio components
audio_streamer.start()
audio_playback.start()

# Toggle pressure states for testing
def toggle_states():
    states = [
        {"local": False, "remote": True},   # Case 2: Remote pressure only
        {"local": True, "remote": True},    # Case 1: Both have pressure
        {"local": True, "remote": False},   # Case 3: Local pressure only
        {"local": False, "remote": False},  # Case 4: No pressure
    ]
    
    for i, state in enumerate(states):
        print(f"\nTest case {i+1}: Local pressure = {state['local']}, Remote pressure = {state['remote']}")
        system_state.update_local_state({"pressure": state["local"]})
        system_state.update_remote_state({"pressure": state["remote"]})
        time.sleep(10)  # Give each state 10 seconds

try:
    # Start state toggling in a separate thread
    toggle_thread = threading.Thread(target=toggle_states)
    toggle_thread.daemon = True
    toggle_thread.start()
    
    # Keep main thread running
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Test interrupted by user")
finally:
    # Clean up
    audio_playback.stop()
    audio_streamer.stop()
