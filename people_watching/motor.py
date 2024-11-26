"""
This file checks received_osc object and moves the motors if
pressure has been false for more then a delay.
"""
import threading
import time
from shared_variables import received_osc, local_osc

class MotorController:
    def __init__(self, serial_connection, required_duration=1, check_interval=0.1):
        self.serial_connection = serial_connection
        self.required_duration = required_duration
        self.check_interval = check_interval
        self.last_false_time = None
        self.thread = threading.Thread(target=self._monitor_pressure)
        self.thread.daemon = True  # Ensures thread stops when the main program exits
        self.running = False
        self.moving = False

    def start(self):
        self.running = True
        self.thread.start()

    def stop(self):
        self.running = False
        self.thread.join()

    def _monitor_pressure(self):
        while self.running:
            if local_osc.get("pressure", True):  # Default to True if key is not present
                if self.last_false_time is None:
                    self.last_false_time = time.time()
                    print("pressure detected")
                elif time.time() - self.last_false_time >= self.required_duration:
                    # only move the motors if the other device is being used
                    print("pressure timer done")
                    if not received_osc["pressure"] and local_osc["pressure"]:
                        # print("sending serial to motors")
                        self.moving = True
                        self._trigger_motor({"y": received_osc["y"], "z": received_osc["z"]})  # Replace with dynamic data if needed
                        self.last_false_time = None  # Reset timer after execution
            else:
                self.moving = False
                self.last_false_time = None  # Reset timer if "pressure" is True

            time.sleep(self.check_interval)

    def _trigger_motor(self, data):
        try:
            y = data.get("y", 0)
            z = data.get("z", 0)
            message = f"{y},{z}\n"
            print("sending the arduino: {message.encode()}")
            self.serial_connection.write(message.encode())
        except Exception as e:
            print(f"Error in _trigger_motor: {e}")
