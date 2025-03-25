"""
This file checks received_osc object and moves the motors if
pressure has been false for more then a delay.
"""
import threading
import time
from system_state import system_state

class MotorController:
    def __init__(self, serial_connection, required_duration=1, check_interval=0.1):
        self.serial_connection = serial_connection
        self.required_duration = required_duration
        self.check_interval = check_interval
        self.last_false_time = None
        self.thread = threading.Thread(target=self._monitor_pressure)
        self.thread.daemon = True
        self.running = False
        self.moving = False

        # Register as an observer to get updates
        system_state.add_observer(self._on_state_change)


    def start(self):
        self.running = True
        self.thread.start()

    def stop(self):
        self.running = False
        self.thread.join()

    def _on_state_change(self, changed_state):
        """Handle state changes."""
        # Optional: React to specific state changes
        pass

    def _monitor_pressure(self):
        while self.running:
            # Get current states
            local_state = system_state.get_local_state()
            remote_state = system_state.get_remote_state()
            
            if local_state["pressure"]:
                if self.last_false_time is None:
                    self.last_false_time = time.time()
                    print("pressure detected")
                elif time.time() - self.last_false_time >= self.required_duration:
                    print("pressure timer done")
                    if not remote_state["pressure"] and local_state["pressure"]:
                        self.moving = True
                        self._trigger_motor({
                            "y": remote_state["y"], 
                            "z": remote_state["z"]
                        })
                        self.last_false_time = None
            else:
                self.moving = False
                self.last_false_time = None
            
            time.sleep(self.check_interval)

    def _trigger_motor(self, data):
        try:
            y = data.get("y", 0)
            z = data.get("z", 0)
            message = f"{y},{z}\n"
            self.serial_connection.write(message.encode())
            self.serial_connection.flush()  # Wait until all data is written
            time.sleep(0.1)  # Small delay to ensure complete
        except Exception as e:
            print(f"Error in _trigger_motor: {e}")
