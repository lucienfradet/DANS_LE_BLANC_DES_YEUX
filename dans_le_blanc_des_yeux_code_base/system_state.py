"""
Thread-safe state management for the Dans le Blanc des Yeux project.
Uses a singleton pattern to ensure a single state instance across the application.
"""

import threading
import configparser
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
            "moving": False
        }
        
        self._remote_device = {
            "y": 0,
            "z": 0,
            "pressure": True,
            "connected": False  # Track if remote device is connected
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
            for key, value in data.items():
                if key in self._local_device:
                    self._local_device[key] = value
        
        self._notify_observers("local")
    
    def update_remote_state(self, data: Dict[str, Any]) -> None:
        """Update the remote device state."""
        with self._state_lock:
            for key, value in data.items():
                if key in self._remote_device:
                    self._remote_device[key] = value
        
        self._notify_observers("remote")
    
    def update_connection_status(self, connected: bool) -> None:
        """Update the connection status for the remote device."""
        with self._state_lock:
            self._remote_device["connected"] = connected
        
        self._notify_observers("connection")
    
    def get_config(self) -> configparser.ConfigParser:
        """Get the configuration object."""
        return self._config
    
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
