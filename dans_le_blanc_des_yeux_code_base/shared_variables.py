import configparser

# Shared state variables
received_osc = {"y": 0, "z": 0, "pressure": True, "eyes_detected": False, "state": "HOME"}
local_osc = {"y": 0, "z": 0, "pressure": False, "eyes_detected": False, "state": "HOME"}

def update_local_osc(parsed_data):
    """Update local state with parsed data"""
    global local_osc
    # Update only the keys present in parsed_data
    for key, value in parsed_data.items():
        if key in local_osc:
            local_osc[key] = value

def update_recieved_osc(parsed_data):
    """Update received state with parsed data"""
    global received_osc
    # Update only the keys present in parsed_data
    for key, value in parsed_data.items():
        if key in received_osc:
            received_osc[key] = value

# Configuration
config = configparser.ConfigParser()
config.read('config.ini')

# System state and events
class SystemState:
    """Singleton for tracking overall system state"""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SystemState, cls).__new__(cls)
            cls._instance.initialize()
        return cls._instance
    
    def initialize(self):
        """Initialize system state"""
        self.current_state = "HOME"
        self.transition_active = False
        self.transition_progress = 0.0
        self.transition_duration = 1.0  # seconds
        self.last_transition_time = 0
        
        # Event callbacks
        self.state_change_callbacks = []
    
    def get_state(self):
        """Get current system state"""
        return self.current_state
    
    def set_state(self, new_state):
        """Set system state with transition"""
        if new_state != self.current_state:
            old_state = self.current_state
            self.current_state = new_state
            self.transition_active = True
            self.transition_progress = 0.0
            self.last_transition_time = 0
            
            # Update local state
            local_osc["state"] = new_state
            
            # Notify state change callbacks
            for callback in self.state_change_callbacks:
                callback(old_state, new_state)
    
    def update_transition(self, delta_time):
        """Update transition progress"""
        if self.transition_active:
            self.transition_progress += delta_time / self.transition_duration
            if self.transition_progress >= 1.0:
                self.transition_progress = 1.0
                self.transition_active = False
    
    def register_state_change_callback(self, callback):
        """Register a callback for state changes"""
        if callback not in self.state_change_callbacks:
            self.state_change_callbacks.append(callback)
    
    def unregister_state_change_callback(self, callback):
        """Unregister a state change callback"""
        if callback in self.state_change_callbacks:
            self.state_change_callbacks.remove(callback)

# Get system state singleton
def get_system_state():
    """Get the SystemState singleton instance"""
    return SystemState()
