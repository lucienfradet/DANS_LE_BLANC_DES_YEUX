"""
This file monitors the system state and moves motors when appropriate.
When a device detects the other device has pressure=true while it doesn't,
it moves its motors to match the orientation of the pressured device.

Usage:
    from motor import MotorController
    motor_controller = MotorController(serial_handler)
    motor_controller.start()
"""
import threading
import time
from system_state import system_state

class MotorController:
    def __init__(self, serial_connection, required_duration=1, check_interval=0.1, motion_timeout=2.0,
                 y_reverse=True, y_min_input=-10, y_max_input=60, y_min_output=-30, y_max_output=80):
        self.serial_connection = serial_connection
        self.required_duration = required_duration  # Duration to wait before triggering motors
        self.check_interval = check_interval  # Time between state checks
        self.motion_timeout = motion_timeout  # Reduced from 3.0 to 2.0 seconds
        
        # Y-axis transformation parameters
        self.y_reverse = y_reverse
        self.y_min_input = y_min_input
        self.y_max_input = y_max_input
        self.y_min_output = y_min_output
        self.y_max_output = y_max_output
        
        self.remote_pressure_start_time = None  # Timestamp when remote pressure was first detected
        self.last_movement_time = 0  # Timestamp of last movement command
        self.movement_min_interval = 1.0  # Minimum time between movement commands
        self.movement_start_time = 0  # Track when movement started
        self.max_movement_duration = 5.0  # Maximum time a movement can take before forced reset
        
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
                
                # Add safety check for stuck moving state
                if self.moving and time.time() - self.movement_start_time > self.max_movement_duration:
                    print(f"WARNING: Movement exceeded max duration ({self.max_movement_duration}s). Force resetting.")
                    self._motion_complete()
                
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

    def _transform_y_value(self, y_value):
        """Transform the Y value based on configuration parameters.
        
        This function:
        1. Applies direction reversal if configured (reflection around midpoint)
        2. Clamps the input value to the specified range
        3. Maps the value from input range to output range
        """
        original_y = y_value
        
        # Apply reversal if needed (reflection around the midpoint of the input range)
        if self.y_reverse:
            # Calculate the midpoint of the input range
            midpoint = (self.y_max_input + self.y_min_input) / 2
            
            # Reflect the value around the midpoint
            y_value = (self.y_max_input + self.y_min_input) - y_value
            
            print(f"Y value reflected: {original_y} → {y_value} (around midpoint {midpoint})")
            
        # Clamp input value to specified range
        y_value = max(self.y_min_input, min(y_value, self.y_max_input))
        
        # Map from input range to output range
        input_range = self.y_max_input - self.y_min_input
        output_range = self.y_max_output - self.y_min_output
        
        # If input range is zero, avoid division by zero
        if input_range == 0:
            mapped_value = self.y_min_output
        else:
            normalized = (y_value - self.y_min_input) / input_range
            mapped_value = self.y_min_output + (normalized * output_range)
        
        # Round to nearest integer for motor control
        return round(mapped_value)

    def _start_movement(self, y_angle, z_angle):
        """Start a motor movement sequence."""
        # Update state
        self.moving = True
        self.movement_start_time = time.time()  # Track when movement started
        system_state.update_local_state({"moving": True})
        self.last_movement_time = time.time()
        
        # Transform the Y angle value
        transformed_y = self._transform_y_value(y_angle)
        
        print(f"Motor - Starting movement at {time.time():.2f} - Y={y_angle} (transformed to {transformed_y}), Z={z_angle}")
        
        # Send movement command with transformed Y value
        success = self._send_motor_command(transformed_y, z_angle)
        
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
            # print(f"Sending to Arduino: {message.strip()}")
            
            # Send the message
            result = self.serial_connection.write(message)
            
            # Check if write was successful
            if not result:
                print("Failed to write to serial port")
                return False
                
            # Update system state with the command that was sent
            system_state.update_motor_command(y_angle, z_angle)
                
            return True
        except Exception as e:
            print(f"Error sending motor commands: {e}")
            return False
    
    def _motion_complete(self):
        """Called when motor motion is complete or failed."""
        duration = time.time() - self.movement_start_time if hasattr(self, 'movement_start_time') else 0
        print(f"Motor - Movement complete after {duration:.2f}s")
        
        self.moving = False
        system_state.update_local_state({"moving": False})
        self.motion_timer = None
