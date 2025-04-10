"""
Thread-safe state management for the Dans le Blanc des Yeux project.
Uses a singleton pattern to ensure a single state instance across the application.
with debounce
"""

import threading
import configparser
import time  # Import for timestamp tracking
from typing import Dict, Any, List, Callable, Optional

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
        
        # Track pressure state changes for debouncing
        self._local_pressure_change_time = time.time()
        self._remote_pressure_change_time = time.time()
        self._pressure_debounce_time = 1.0  # Default value in seconds
        
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
        """Update the local device state and track pressure changes for debounce."""
        with self._state_lock:
            changed = False
            
            # Check for pressure change specifically
            if "pressure" in data and data["pressure"] != self._local_device["pressure"]:
                # Update pressure change time
                self._local_pressure_change_time = time.time()
                print(f"Local pressure changed to {data['pressure']} (debounce timer started)")
            
            # Update all state data
            for key, value in data.items():
                if key in self._local_device and self._local_device[key] != value:
                    self._local_device[key] = value
                    changed = True
        
        if changed:
            self._notify_observers("local")
    
    def update_remote_state(self, data: Dict[str, Any]) -> None:
        """
        Update the remote device state. Remote pressure updates are considered
        already debounced by the remote device, so we don't apply additional debouncing.
        """
        with self._state_lock:
            changed = False
            
            # Update all state data without starting a debounce timer for remote pressure
            for key, value in data.items():
                if key in self._remote_device and self._remote_device[key] != value:
                    # For pressure changes, just log without debouncing
                    if key == "pressure":
                        print(f"Remote pressure changed to {value} (accepted immediately)")
                    
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
    
    # New methods for pressure debounce logic
    
    def set_pressure_debounce_time(self, debounce_time: float) -> None:
        """Set the debounce time for pressure changes."""
        with self._state_lock:
            self._pressure_debounce_time = max(0.0, float(debounce_time))
        print(f"Set pressure debounce time to {self._pressure_debounce_time} seconds")
    
    def get_pressure_debounce_time(self) -> float:
        """Get the current pressure debounce time."""
        with self._state_lock:
            return self._pressure_debounce_time
    
    def is_pressure_state_stable(self) -> bool:
        """
        Check if the local pressure state has been stable for the debounce time.
        Remote pressure is considered already debounced by the remote device.
        
        Returns:
            True if pressure state is stable (only checks local debounce)
        """
        with self._state_lock:
            current_time = time.time()
            local_time_since_change = current_time - self._local_pressure_change_time
            
            # Only check local pressure debounce since remote is considered already debounced
            return local_time_since_change >= self._pressure_debounce_time
    
    def is_local_pressure_stable(self) -> bool:
        """
        Check if the local pressure state has been stable for at least the debounce time.
        
        Returns:
            True if local pressure state is stable, False otherwise
        """
        with self._state_lock:
            current_time = time.time()
            local_time_since_change = current_time - self._local_pressure_change_time
            return local_time_since_change >= self._pressure_debounce_time
    
    def is_remote_pressure_stable(self) -> bool:
        """
        Check if the remote pressure state has been stable for at least the debounce time.
        
        Returns:
            True if remote pressure state is stable, False otherwise
        """
        with self._state_lock:
            current_time = time.time()
            remote_time_since_change = current_time - self._remote_pressure_change_time
            return remote_time_since_change >= self._pressure_debounce_time


# Create a singleton instance that can be imported directly
system_state = SystemState()
