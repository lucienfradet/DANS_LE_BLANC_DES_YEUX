"""
Robust OSC communication handler with timeout management, heartbeat,
and proper error recovery.
"""

import time
import threading
import socket
from typing import Dict, Any, Optional, Callable, List, Tuple

from pythonosc.udp_client import SimpleUDPClient
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

from system_state import system_state
from serial_handler import SerialHandler, parse_serial_data

# Constants
OSC_SERVER_PORT = 8888
OSC_CLIENT_PORT = 9999
HEARTBEAT_INTERVAL = 5.0  # seconds
CONNECTION_TIMEOUT = 10.0  # seconds
MAX_RECONNECT_ATTEMPTS = 5

class OSCHandler:
    def __init__(self, remote_ip: str, serial_handler: SerialHandler):
        self.remote_ip = remote_ip
        self.serial_handler = serial_handler
        
        # OSC client and server
        self.osc_client = None
        self.osc_server = None
        self.server_thread = None
        
        # Connection tracking
        self.remote_connected = False
        self.last_heartbeat_received = 0
        self.heartbeat_lock = threading.Lock()
        
        # Threads
        self.threads = []
        self.running = False
        self.stop_event = threading.Event()
        
        # Callbacks
        self.connection_status_callback = None
    
    def start(self) -> bool:
        """Start the OSC handler and all its threads."""
        print("Starting OSC Handler...")
        self.running = True
        self.stop_event.clear()
        
        # Initialize OSC client
        if not self._init_osc_client():
            print("Failed to initialize OSC client")
            return False
        
        # Start OSC server
        if not self._start_osc_server():
            print("Failed to start OSC server")
            return False
        
        # Start heartbeat thread
        heartbeat_thread = threading.Thread(target=self._heartbeat_loop)
        heartbeat_thread.daemon = True
        heartbeat_thread.start()
        self.threads.append(heartbeat_thread)
        
        # Start connection monitor thread
        monitor_thread = threading.Thread(target=self._connection_monitor_loop)
        monitor_thread.daemon = True
        monitor_thread.start()
        self.threads.append(monitor_thread)
        
        # Start serial data reader thread
        serial_thread = threading.Thread(target=self._serial_read_loop)
        serial_thread.daemon = True
        serial_thread.start()
        self.threads.append(serial_thread)
        
        print("OSC Handler started successfully")
        return True
    
    def stop(self) -> None:
        """Stop all OSC handler threads and clean up resources."""
        print("Stopping OSC Handler...")
        self.running = False
        self.stop_event.set()
        
        # Stop OSC server
        if self.osc_server:
            self.osc_server.shutdown()
        
        # Wait for all threads to finish
        for thread in self.threads:
            thread.join(timeout=1.0)
        
        print("OSC Handler stopped")
    
    def register_connection_callback(self, callback: Callable[[bool], None]) -> None:
        """Register a callback for connection status changes."""
        self.connection_status_callback = callback
    
    def send_data(self, data: Dict[str, Any], max_retries: int = 3) -> bool:
        """Send data to remote device with retry mechanism."""
        if not self.osc_client:
            return False
        
        for attempt in range(max_retries):
            try:
                self.osc_client.send_message("/data", [
                    data["y"], data["z"], int(data["pressure"])
                ])
                return True
            except (socket.error, OSError) as e:
                print(f"Error sending OSC data (attempt {attempt+1}/{max_retries}): {str(e)}")
                if attempt == max_retries - 1:
                    # Last attempt failed, update connection status
                    self._update_connection_status(False)
                    return False
                time.sleep(0.5)
    
    def _init_osc_client(self, max_retries: int = 3) -> bool:
        """Initialize OSC client with retry mechanism."""
        for attempt in range(max_retries):
            try:
                print(f"Initializing OSC client to {self.remote_ip}:{OSC_CLIENT_PORT} (Attempt {attempt+1}/{max_retries})")
                self.osc_client = SimpleUDPClient(self.remote_ip, OSC_CLIENT_PORT)
                
                # Test connection by sending a heartbeat
                self.osc_client.send_message("/heartbeat", [1])
                print(f"OSC client initialized")
                return True
            except (socket.error, OSError) as e:
                print(f"Failed to initialize OSC client: {str(e)}")
                if attempt < max_retries - 1:
                    print(f"Retrying in 2 seconds...")
                    time.sleep(2)
        
        print(f"Failed to initialize OSC client after {max_retries} attempts")
        return False
    
    def _start_osc_server(self, max_retries: int = 3) -> bool:
        """Start OSC server with retry mechanism."""
        for attempt in range(max_retries):
            try:
                print(f"Starting OSC server on port {OSC_SERVER_PORT} (Attempt {attempt+1}/{max_retries})")
                
                # Create dispatcher
                dispatcher = Dispatcher()
                dispatcher.map("/data", self._handle_data)
                dispatcher.map("/heartbeat", self._handle_heartbeat)
                
                # Create server with timeout
                self.osc_server = ThreadingOSCUDPServer(
                    ("0.0.0.0", OSC_SERVER_PORT), 
                    dispatcher
                )
                self.osc_server.socket.settimeout(0.5)  # Short timeout for responsive shutdown
                
                # Start server in a separate thread
                self.server_thread = threading.Thread(target=self._run_server)
                self.server_thread.daemon = True
                self.server_thread.start()
                
                print(f"OSC server started on port {OSC_SERVER_PORT}")
                return True
            except (socket.error, OSError) as e:
                print(f"Failed to start OSC server: {str(e)}")
                if attempt < max_retries - 1:
                    print(f"Retrying in 2 seconds...")
                    time.sleep(2)
        
        print(f"Failed to start OSC server after {max_retries} attempts")
        return False
    
    def _run_server(self) -> None:
        """Run the OSC server with exception handling."""
        try:
            self.osc_server.serve_forever()
        except Exception as e:
            if self.running:  # Only log if not intentionally stopped
                print(f"OSC server error: {str(e)}")
    
    def _handle_data(self, unused_addr, y, z, pressure) -> None:
        """Handle incoming data from remote device."""
        # Update last heartbeat time
        with self.heartbeat_lock:
            self.last_heartbeat_received = time.time()
            was_connected = self.remote_connected
            self.remote_connected = True
        
        # Update connection status if changed
        if not was_connected:
            self._update_connection_status(True)
        
        # Parse and store data
        received_data = {"y": y, "z": z, "pressure": bool(pressure)}
        system_state.update_remote_state(received_data)
        print(f"Received from OSC: {received_data}")
    
    def _handle_heartbeat(self, unused_addr, value) -> None:
        """Handle heartbeat from remote device."""
        with self.heartbeat_lock:
            self.last_heartbeat_received = time.time()
            was_connected = self.remote_connected
            self.remote_connected = True
        
        # Update connection status if changed
        if not was_connected:
            self._update_connection_status(True)
    
    def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats to remote device."""
        while not self.stop_event.is_set():
            if self.osc_client:
                try:
                    self.osc_client.send_message("/heartbeat", [1])
                except Exception as e:
                    print(f"Error sending heartbeat: {str(e)}")
            
            # Sleep for heartbeat interval, checking stop flag periodically
            for _ in range(int(HEARTBEAT_INTERVAL * 10)):
                if self.stop_event.is_set():
                    break
                time.sleep(0.1)
    
    def _connection_monitor_loop(self) -> None:
        """Monitor connection status based on heartbeats."""
        while not self.stop_event.is_set():
            with self.heartbeat_lock:
                if (self.remote_connected and 
                    time.time() - self.last_heartbeat_received > CONNECTION_TIMEOUT):
                    print("Remote device connection timeout")
                    self.remote_connected = False
                    self._update_connection_status(False)
            
            # Check connection every second
            time.sleep(1)
    
    def _update_connection_status(self, connected: bool) -> None:
        """Update connection status and notify via callback if registered."""
        print(f"Remote device connection: {'Connected' if connected else 'Disconnected'}")
        
        if self.connection_status_callback:
            self.connection_status_callback(connected)
    
    def _serial_read_loop(self) -> None:
        """Read data from Arduino and send via OSC."""
        while not self.stop_event.is_set():
            if not self.serial_handler.connected:
                time.sleep(1)
                continue
            
            # Only request data if we're not moving
            local_state = system_state.get_local_state()
            if local_state.get("moving", False):
                time.sleep(0.1)
                continue
            
            # Request data from Arduino
            response = self.serial_handler.send_command_wait_response(".\n", timeout=1.0)
            if not response:
                time.sleep(0.1)
                continue
            
            # Parse the response
            parsed_data = parse_serial_data(response)
            if parsed_data:
                # Update local state
                system_state.update_local_state(parsed_data)
                
                # Send to remote device
                self.send_data(parsed_data)
            
            # Brief pause to prevent flooding
            time.sleep(0.03)  # ~30Hz


def run_osc_handler(remote_ip: str, arduino_port: str = "/dev/ttyACM0") -> Tuple[OSCHandler, SerialHandler]:
    """Initialize and run the OSC handler with given configuration."""
    # Initialize serial handler
    serial_handler = SerialHandler(port=arduino_port, baudrate=9600)
    if not serial_handler.connect():
        print("Warning: Failed to connect to Arduino. Continuing anyway...")
    
    # Initialize OSC handler
    osc_handler = OSCHandler(remote_ip, serial_handler)
    osc_handler.start()
    
    return osc_handler, serial_handler
