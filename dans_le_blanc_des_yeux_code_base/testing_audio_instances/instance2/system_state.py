"""
Thread-safe state management for the Dans le Blanc des Yeux project.
Uses a singleton pattern to ensure a single state instance across the application.
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
    
    def get_local_state(self) -> Dict[str, Any]:
        """Get a copy of the local device state."""
        with self._state_lock:
            return self._local_device.copy()
    
    def get_remote_state(self) -> Dict[str, Any]:
        """Get a copy of the remote device state."""
        with self._state_lock:
            return self._remote_device.copy()
    
    def update_local_state(self, data: Dict[str, Any]) -> None:
        """Update the local device state."""
        with self._state_lock:
            changed = False
            for key, value in data.items():
                if key in self._local_device and self._local_device[key] != value:
                    self._local_device[key] = value
                    changed = True
        
        if changed:
            self._notify_observers("local")
    
    def update_remote_state(self, data: Dict[str, Any]) -> None:
        """Update the remote device state."""
        with self._state_lock:
            changed = False
            for key, value in data.items():
                if key in self._remote_device and self._remote_device[key] != value:
                    self._remote_device[key] = value
                    changed = True
        
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
