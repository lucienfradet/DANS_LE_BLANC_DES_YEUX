"""
This file monitors the system state and moves motors when appropriate.
When a device detects the other device has pressure=true while it doesn't,
it moves its motors to match the orientation of the pressured device.
with debounce

Usage:
    from motor import MotorController
    motor_controller = MotorController(serial_handler)
    motor_controller.start()
"""
import threading
import time
from system_state import system_state

class MotorController:
    def __init__(self, serial_connection, motion_timeout=2.0):
        self.serial_connection = serial_connection
        self.motion_timeout = motion_timeout  # How long to wait for motion to complete
        
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
        pass  # We're using the monitor thread for state checking instead
        
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
                time.sleep(0.1)  # Check interval
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
        # 4. Local pressure state is stable (we only debounce local pressure now)
        if (remote_state["pressure"] and 
            not local_state["pressure"] and 
            remote_state["connected"]):
            
            # Check if local pressure state has been stable for the required time
            if system_state.is_local_pressure_stable():
                # Check if we can move now (not already moving and min interval has passed)
                if (not self.moving and 
                        current_time - self.last_movement_time >= self.movement_min_interval):
                    
                    print(f"Moving motors to match remote orientation: Y={remote_state['y']}, Z={remote_state['z']}")
                    self._start_movement(remote_state["y"], remote_state["z"])

    def _start_movement(self, y_angle, z_angle):
        """Start a motor movement sequence."""
        # Update state
        self.moving = True
        self.movement_start_time = time.time()  # Track when movement started
        system_state.update_local_state({"moving": True})
        self.last_movement_time = time.time()
        
        print(f"Motor - Starting movement at {time.time():.2f} - Y={y_angle}, Z={z_angle}")
        
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
