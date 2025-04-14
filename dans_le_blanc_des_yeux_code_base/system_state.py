"""
Thread-safe state management for the Dans le Blanc des Yeux project.
Uses a singleton pattern to ensure a single state instance across the application.
debounce v3
"""

import threading
import configparser
import time  # Added import for timestamp
from typing import Dict, Any, List, Callable

class SystemState:
    """Thread-safe singleton class to manage system state."""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(SystemState, cls).__new__(cls)
                cls._instance._initialize()
            return cls._instance
    
    def _initialize(self):
        """Initialize the state values."""
        self._state_lock = threading.Lock()
        
        # State values
        self._local_device = {
            "y": 0,
            "z": 0,
            "pressure": False,
            "moving": False  # Track if motors are currently moving
        }
        
        self._remote_device = {
            "y": 0,
            "z": 0,
            "pressure": False,
            "connected": False  # Track if remote device is connected
        }
        
        # Store last motor command for visualization
        self._last_motor_command = {
            "y": 0,
            "z": 0,
            "timestamp": 0
        }
        
        # Configuration
        self._config = configparser.ConfigParser()
        self._config.read('config.ini')
        
        # Observers for state changes
        self._observers = []
        
        # Pressure debounce settings
        self._pressure_debounce_time = 1.0  # Default debounce time in seconds
        self._pressure_last_change_time = 0  # Timestamp of last pressure state change
        self._pressure_pending_value = None  # Pending pressure value awaiting debounce
        
        # Idle mode tracking
        self._is_idle_mode = True  # Start in idle mode
        self._last_activity_time = 0  # Time of last detected activity
        self._idle_timeout = 30.0  # Time in seconds before switching to idle mode
    
    def get_local_state(self) -> Dict[str, Any]:
        """Get a copy of the local device state."""
        with self._state_lock:
            return self._local_device.copy()
    
    def get_remote_state(self) -> Dict[str, Any]:
        """Get a copy of the remote device state."""
        with self._state_lock:
            return self._remote_device.copy()
    
    def set_pressure_debounce_time(self, debounce_time: float) -> None:
        """Set the pressure debounce time in seconds."""
        with self._state_lock:
            self._pressure_debounce_time = max(0.0, float(debounce_time))
            print(f"Pressure debounce time set to {self._pressure_debounce_time} seconds")
    
    def set_idle_timeout(self, timeout_seconds: float) -> None:
        """Set the idle timeout in seconds."""
        with self._state_lock:
            self._idle_timeout = max(5.0, float(timeout_seconds))
            print(f"Idle timeout set to {self._idle_timeout} seconds")
    
    def is_idle_mode(self) -> bool:
        """Check if the system is in idle mode."""
        with self._state_lock:
            # Check if we need to transition to idle mode
            current_time = time.time()
            if not self._is_idle_mode and (current_time - self._last_activity_time) >= self._idle_timeout:
                self._is_idle_mode = True
                print(f"System switched to idle mode after {self._idle_timeout} seconds of inactivity")
                self._notify_observers("idle_mode")
            
            return self._is_idle_mode
    
    def update_activity(self) -> None:
        """Mark that there was user activity, possibly exiting idle mode."""
        with self._state_lock:
            current_time = time.time()
            self._last_activity_time = current_time
            
            # If we were in idle mode, exit it
            if self._is_idle_mode:
                self._is_idle_mode = False
                print("System exited idle mode due to activity")
                self._notify_observers("idle_mode")
    
    def update_local_state(self, data: Dict[str, Any]) -> None:
        """Update the local device state with debounce for pressure changes."""
        current_time = time.time()
        changed = False
        active = False
        
        # Check if this update indicates activity
        if "pressure" in data and data["pressure"]:
            active = True
        
        # Handle pressure with debounce
        if "pressure" in data:
            new_pressure = data["pressure"]
            with self._state_lock:
                # If this would be a change in pressure state
                if self._local_device["pressure"] != new_pressure:
                    # First time seeing this pressure value or resetting during debounce
                    if self._pressure_pending_value != new_pressure:
                        self._pressure_pending_value = new_pressure
                        self._pressure_last_change_time = current_time
                        print(f"Pending pressure change to {new_pressure}, waiting for debounce ({self._pressure_debounce_time}s)")
                    # If it's been stable for the debounce period, apply it
                    elif current_time - self._pressure_last_change_time >= self._pressure_debounce_time:
                        print(f"Applying debounced pressure change to {new_pressure}")
                        self._local_device["pressure"] = new_pressure
                        self._pressure_pending_value = None
                        changed = True
                        
                        # This is considered activity
                        active = True
                
                # Process other state changes immediately
                for key, value in data.items():
                    if key != "pressure" and key in self._local_device and self._local_device[key] != value:
                        self._local_device[key] = value
                        changed = True
        else:
            # No pressure in data, process normally
            with self._state_lock:
                for key, value in data.items():
                    if key in self._local_device and self._local_device[key] != value:
                        self._local_device[key] = value
                        changed = True
        
        # Update activity status
        if active:
            self.update_activity()
        
        if changed:
            self._notify_observers("local")
    
    def update_remote_state(self, data: Dict[str, Any]) -> None:
        """Update the remote device state."""
        active = False
        
        with self._state_lock:
            changed = False
            for key, value in data.items():
                if key in self._remote_device and self._remote_device[key] != value:
                    self._remote_device[key] = value
                    changed = True
            
            # If remote has pressure, this is activity
            if "pressure" in data and data["pressure"]:
                active = True
        
        # Update activity status if there's remote pressure
        if active:
            self.update_activity()
        
        if changed:
            self._notify_observers("remote")
    
    def update_connection_status(self, connected: bool) -> None:
        """Update the connection status for the remote device."""
        with self._state_lock:
            if self._remote_device["connected"] != connected:
                self._remote_device["connected"] = connected
                self._notify_observers("connection")
    
    def get_config(self) -> configparser.ConfigParser:
        """Get the configuration object."""
        return self._config
    
    def get_last_motor_command(self) -> Dict[str, Any]:
        """Get the last motor command that was sent."""
        with self._state_lock:
            return self._last_motor_command.copy()
    
    def update_motor_command(self, y: int, z: int) -> None:
        """Update the last motor command information."""
        with self._state_lock:
            self._last_motor_command = {
                "y": y,
                "z": z,
                "timestamp": time.time()
            }
        
        # Motor movement is activity
        self.update_activity()
        
        # Notify observers of motor command
        self._notify_observers("motor_command")

    def update_audio_state(self, data: Dict[str, Any]) -> None:
        """Update the audio state information."""
        with self._state_lock:
            if not hasattr(self, '_audio_state'):
                self._audio_state = {
                    "audio_sending": False,
                    "audio_mic": "None",
                    "audio_muted_channel": "both"
                }
            
            changed = False
            for key, value in data.items():
                if key not in self._audio_state or self._audio_state[key] != value:
                    self._audio_state[key] = value
                    changed = True
        
        if changed:
            self._notify_observers("audio")
        
    def get_audio_state(self) -> Dict[str, Any]:
        """Get a copy of the audio state."""
        with self._state_lock:
            if not hasattr(self, '_audio_state'):
                self._audio_state = {
                    "audio_sending": False,
                    "audio_mic": "None",
                    "audio_muted_channel": "both"
                }
            return self._audio_state.copy()
    
    def add_observer(self, callback: Callable[[str], None]) -> None:
        """Add an observer to be notified of state changes."""
        self._observers.append(callback)
    
    def remove_observer(self, callback: Callable[[str], None]) -> None:
        """Remove an observer."""
        if callback in self._observers:
            self._observers.remove(callback)
    
    def _notify_observers(self, changed_state: str) -> None:
        """Notify all observers of a state change."""
        for observer in self._observers:
            observer(changed_state)


# Create a singleton instance that can be imported directly
system_state = SystemState()
