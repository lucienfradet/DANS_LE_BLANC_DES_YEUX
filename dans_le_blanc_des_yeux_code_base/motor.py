"""
This file monitors the system state and moves motors when appropriate.
When a device detects the other device has pressure=true while it doesn't,
it moves its motors to match the orientation of the pressured device.
"""
import threading
import time
from system_state import system_state

class MotorController:
    def __init__(self, serial_connection, required_duration=1, check_interval=0.1, motion_timeout=3.0):
        self.serial_connection = serial_connection
        self.required_duration = required_duration  # Duration to wait before triggering motors (prevents false positives)
        self.check_interval = check_interval  # Time between state checks
        self.motion_timeout = motion_timeout  # Timeout for motor movements
        
        self.remote_pressure_start_time = None  # Timestamp when remote pressure was first detected
        self.last_movement_time = 0  # Timestamp of last movement command
        self.movement_min_interval = 1.0  # Minimum time between movement commands
        
        self.thread = threading.Thread(target=self._monitor_state)
        self.thread.daemon = True
        self.running = False
        self.moving = False
        self.motion_timer = None  # Timer for motion completion

        # Register as an observer to get updates
        system_state.add_observer(self._on_state_change)

    def start(self):
        """Start the motor controller thread."""
        self.running = True
        self.thread.start()
        print("Motor controller started")

    def stop(self):
        """Stop the motor controller thread."""
        self.running = False
        
        # Cancel any pending motion timer
        if self.motion_timer:
            self.motion_timer.cancel()
            
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        print("Motor controller stopped")

    def _on_state_change(self, changed_state):
        """Handle state changes immediately when notified by system_state."""
        # Reset timer if conditions change
        if changed_state == "remote" or changed_state == "local":
            self._check_pressure_conditions()
        
    def _check_pressure_conditions(self):
        """Check if pressure conditions are still valid for movement."""
        local_state = system_state.get_local_state()
        remote_state = system_state.get_remote_state()
        
        # If conditions for movement aren't met, reset timer
        if not (remote_state["pressure"] and not local_state["pressure"]):
            if self.remote_pressure_start_time is not None:
                print("Conditions for movement no longer met, resetting timer")
                self.remote_pressure_start_time = None

    def _monitor_state(self):
        """Monitor system state and trigger motor movements when appropriate."""
        while self.running:
            try:
                # Get current states
                local_state = system_state.get_local_state()
                remote_state = system_state.get_remote_state()
                
                # Process pressure states
                self._process_pressure_states(local_state, remote_state)
                
                # Sleep before next check
                time.sleep(self.check_interval)
            except Exception as e:
                print(f"Error in motor monitoring thread: {e}")
                time.sleep(1.0)  # Sleep longer on error

    def _process_pressure_states(self, local_state, remote_state):
        """Process pressure states and trigger movement if conditions are met."""
        current_time = time.time()
        
        # Check movement conditions:
        # 1. Remote has pressure
        # 2. Local doesn't have pressure
        # 3. Remote is connected
        if remote_state["pressure"] and not local_state["pressure"] and remote_state["connected"]:
            # Start timer if not already started
            if self.remote_pressure_start_time is None:
                self.remote_pressure_start_time = current_time
                print("Remote pressure detected, starting timer")
            
            # Check if we've waited long enough to confirm it's not a false positive
            elif current_time - self.remote_pressure_start_time >= self.required_duration:
                # Check if we can move now
                if (not self.moving and 
                        current_time - self.last_movement_time >= self.movement_min_interval):
                    
                    print(f"Moving motors to match remote orientation: Y={remote_state['y']}, Z={remote_state['z']}")
                    self._start_movement(remote_state["y"], remote_state["z"])

    def _start_movement(self, y_angle, z_angle):
        """Start a motor movement sequence."""
        # Update state
        self.moving = True
        system_state.update_local_state({"moving": True})
        self.last_movement_time = time.time()
        
        # Send movement command
        success = self._send_motor_command(y_angle, z_angle)
        
        if success:
            # Set timer for motion completion
            if self.motion_timer:
                self.motion_timer.cancel()
            self.motion_timer = threading.Timer(self.motion_timeout, self._motion_complete)
            self.motion_timer.daemon = True
            self.motion_timer.start()
        else:
            # Reset moving state on failure
            self._motion_complete()

    def _send_motor_command(self, y_angle, z_angle):
        """Send movement commands to the Arduino."""
        if not self.serial_connection:
            print("Error: No serial connection available")
            return False
            
        try:
            # Format the message: Y,Z coordinates followed by newline
            message = f"{y_angle},{z_angle}\n"
            print(f"Sending to Arduino: {message.strip()}")
            
            # Send the message
            result = self.serial_connection.write(message)
            
            # Check if write was successful
            if not result:
                print("Failed to write to serial port")
                return False
                
            return True
        except Exception as e:
            print(f"Error sending motor commands: {e}")
            return False
    
    def _motion_complete(self):
        """Called when motor motion is complete or failed."""
        print("Motor motion complete")
        self.moving = False
        system_state.update_local_state({"moving": False})
        self.motion_timer = None
