"""
Robust serial communication handler with timeout management.
Handles Arduino connections with proper error recovery.
"""

import serial
import time
import threading
from typing import Optional, Callable, Dict, Any

class SerialHandler:
    def __init__(self, port: str = "/dev/ttyACM0", baudrate: int = 9600, 
                 timeout: float = 1.0, max_retries: int = 5, retry_delay: float = 2.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        
        self.connection = None
        self.connected = False
        self.lock = threading.Lock()
        self.watchdog_thread = None
        self.stop_watchdog = threading.Event()
        self.heartbeat_callback = None
        
    def connect(self) -> bool:
        """
        Connect to the serial device with retry mechanism.
        Returns True if connection successful, False otherwise.
        """
        retry_count = 0
        
        while retry_count < self.max_retries:
            try:
                print(f"Attempting to connect to serial port: {self.port} (Attempt {retry_count + 1}/{self.max_retries})")
                
                # Create new connection with timeout
                self.connection = serial.Serial(
                    port=self.port,
                    baudrate=self.baudrate,
                    timeout=self.timeout
                )
                
                # Wait for Arduino reset
                time.sleep(2)
                
                # Flush any pending data
                self.connection.flushInput()
                self.connection.flushOutput()
                
                # Test connection with a simple command
                if self._test_connection():
                    self.connected = True
                    print(f"Successfully connected to {self.port}")
                    
                    # Start watchdog to monitor connection
                    self._start_watchdog()
                    return True
            except (serial.SerialException, OSError) as e:
                print(f"Failed to connect: {str(e)}")
            
            retry_count += 1
            if retry_count < self.max_retries:
                print(f"Retrying in {self.retry_delay} seconds...")
                time.sleep(self.retry_delay)
        
        print(f"Failed to connect to {self.port} after {self.max_retries} attempts")
        return False
    
    def disconnect(self) -> None:
        """Safely disconnect from the serial device."""
        self.stop_watchdog.set()
        
        if self.watchdog_thread:
            self.watchdog_thread.join(timeout=1.0)
        
        with self.lock:
            if self.connection and self.connection.is_open:
                try:
                    self.connection.close()
                except Exception as e:
                    print(f"Error closing serial connection: {str(e)}")
                finally:
                    self.connected = False
                    print("Serial connection closed")
    
    def write(self, data: str, timeout: float = 2.0) -> bool:
        """
        Write data to the serial connection with timeout.
        Returns True if successful, False otherwise.
        """
        if not self.connected or not self.connection:
            return False
        
        try:
            with self.lock:
                self.connection.write(data.encode())
                self.connection.flush()
            return True
        except (serial.SerialException, OSError) as e:
            print(f"Error writing to serial: {str(e)}")
            self._handle_connection_error()
            return False
    
    def read_line(self, timeout: float = 2.0) -> Optional[str]:
        """
        Read a line from serial with timeout.
        Returns the line as string if successful, None otherwise.
        """
        if not self.connected or not self.connection:
            return None
        
        try:
            with self.lock:
                # Set new timeout for this operation
                old_timeout = self.connection.timeout
                self.connection.timeout = timeout
                
                try:
                    line = self.connection.readline().decode('utf-8').strip()
                    return line if line else None
                finally:
                    # Restore original timeout
                    self.connection.timeout = old_timeout
        except (serial.SerialException, OSError, UnicodeDecodeError) as e:
            print(f"Error reading from serial: {str(e)}")
            self._handle_connection_error()
            return None
    
    def send_command_wait_response(self, command: str, timeout: float = 2.0, 
                                  retries: int = 3) -> Optional[str]:
        """
        Send command and wait for response with timeout and retry mechanism.
        Returns response if successful, None otherwise.
        """
        for attempt in range(retries):
            if not self.write(command):
                continue
                
            response = self.read_line(timeout)
            if response:
                return response
                
            print(f"No response from device, retrying ({attempt+1}/{retries})")
            time.sleep(0.5)
            
        return None
    
    def register_heartbeat_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback to be called when connection is lost/restored."""
        self.heartbeat_callback = callback
    
    def _test_connection(self) -> bool:
        """Test if the connection is working properly."""
        try:
            with self.lock:
                # Clear any pending data
                self.connection.flushInput()
                
                # Send a test command and wait for response
                self.connection.write(b".\n")
                self.connection.flush()
                
                # Wait for response with timeout
                start_time = time.time()
                while (time.time() - start_time) < 3.0:
                    if self.connection.in_waiting:
                        response = self.connection.readline()
                        if response:
                            return True
                    time.sleep(0.1)
                
                return False
        except Exception as e:
            print(f"Connection test failed: {str(e)}")
            return False
    
    def _handle_connection_error(self) -> None:
        """Handle connection errors and trigger reconnection."""
        was_connected = self.connected
        self.connected = False
        
        if was_connected:
            print("Connection lost. Attempting to reconnect.")
            
            # Close the connection if it's still open
            try:
                if self.connection and self.connection.is_open:
                    self.connection.close()
            except Exception:
                pass
                
            # Try to reconnect
            self.connect()
            
            # Notify via callback if registered
            if self.heartbeat_callback:
                self.heartbeat_callback()
    
    def _start_watchdog(self) -> None:
        """Start a watchdog thread to monitor the connection."""
        self.stop_watchdog.clear()
        self.watchdog_thread = threading.Thread(target=self._watchdog_loop)
        self.watchdog_thread.daemon = True
        self.watchdog_thread.start()
    
    def _watchdog_loop(self) -> None:
        """Watchdog loop that periodically checks the connection."""
        while not self.stop_watchdog.is_set():
            if self.connected:
                # Check connection health every 5 seconds
                if not self._test_connection():
                    print("Watchdog detected connection issue")
                    self._handle_connection_error()
            
            # Sleep for 5 seconds between checks
            for _ in range(50):  # 5 seconds with 100ms checks
                if self.stop_watchdog.is_set():
                    break
                time.sleep(0.1)


def parse_serial_data(line: str) -> Optional[Dict[str, Any]]:
    """Parse a line of data from the Arduino."""
    try:
        parts = line.strip().split(", ")
        parsed = {}
        for part in parts:
            key, value = part.split(": ")
            if key in ["y", "z"]:
                parsed[key] = int(float(value))
            elif key == "pressure":
                parsed[key] = value == "1"
        return parsed
    except Exception as e:
        print(f"Error parsing line: {line}, Error: {e}")
        return None
