"""
OSC communication handler with enhanced debugging and connection verification.
"""

import time
import threading
import socket
import subprocess
from typing import Dict, Any, Optional, Callable, List, Tuple

from pythonosc.udp_client import SimpleUDPClient
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

from system_state import system_state
from serial_handler import SerialHandler, parse_serial_data

# Constants
OSC_SERVER_PORT = 8888
OSC_CLIENT_PORT = 9999
HEARTBEAT_INTERVAL = 2.0  # seconds
CONNECTION_TIMEOUT = 5.0  # seconds
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
        
        # Network connectivity check
        self._check_network_connectivity()
    
    def _check_network_connectivity(self):
        """Check if the remote IP is reachable."""
        print(f"\n--- Network Connectivity Check ---")
        print(f"Testing connection to remote device at {self.remote_ip}...")
        
        # Try to ping the remote IP
        try:
            ping_param = "-n 1" if hasattr(socket, 'SOCK_RAW') else "-c 1"
            ping_cmd = f"ping {ping_param} {self.remote_ip}"
            exit_code = subprocess.call(
                ping_cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            if exit_code == 0:
                print(f"✅ Remote device is reachable via ping")
            else:
                print(f"⚠️ WARNING: Remote device does not respond to ping")
        except Exception as e:
            print(f"⚠️ WARNING: Ping test failed: {str(e)}")
        
        # Try to establish a TCP connection to the OSC port
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            result = s.connect_ex((self.remote_ip, OSC_CLIENT_PORT))
            s.close()
            
            if result == 0:
                print(f"✅ Remote OSC port {OSC_CLIENT_PORT} is open and reachable")
            else:
                print(f"⚠️ WARNING: Remote OSC port {OSC_CLIENT_PORT} is not reachable (error code: {result})")
                print(f"   This may prevent OSC communication. Check firewall settings.")
        except Exception as e:
            print(f"⚠️ WARNING: OSC port test failed: {str(e)}")
        
        # Print local network interfaces for debugging
        try:
            print("\n--- Local Network Interfaces ---")
            if hasattr(subprocess, 'check_output'):
                if 'posix' in socket.module.__name__:
                    interfaces = subprocess.check_output("ifconfig || ip addr", shell=True).decode('utf-8')
                else:
                    interfaces = subprocess.check_output("ipconfig", shell=True).decode('utf-8')
                
                # Extract and print just the IP addresses for brevity
                import re
                ip_pattern = r'inet (?:addr:)?(\d+\.\d+\.\d+\.\d+)'
                matches = re.findall(ip_pattern, interfaces)
                
                print("Available IP addresses on this device:")
                for i, ip in enumerate(matches):
                    if ip.startswith('127.'):
                        print(f"  {ip} (localhost)")
                    else:
                        print(f"  {ip}")
        except Exception as e:
            print(f"Could not determine network interfaces: {str(e)}")
        
        print("--- End Network Check ---\n")
    
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
        
        # Update system state with initial connection status
        system_state.update_connection_status(False)
        
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
                # Convert to appropriate types for OSC
                osc_data = [
                    int(data["y"]), 
                    int(data["z"]), 
                    1 if data["pressure"] else 0
                ]
                
                self.osc_client.send_message("/data", osc_data)
                return True
            except (socket.error, OSError, ValueError) as e:
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
                
                # Set a shorter socket timeout for responsive shutdown
                self.osc_server.socket.settimeout(0.5)
                
                # Start server in a separate thread
                self.server_thread = threading.Thread(target=self._run_server)
                self.server_thread.daemon = True
                self.server_thread.start()
                
                # Verify the server is running
                time.sleep(0.5)
                if not self.server_thread.is_alive():
                    raise Exception("Server thread stopped unexpectedly")
                
                print(f"✅ OSC server started on port {OSC_SERVER_PORT}")
                return True
            except (socket.error, OSError) as e:
                print(f"❌ Failed to start OSC server: {str(e)}")
                # Check if port is already in use
                if "Address already in use" in str(e):
                    print("   Port is already in use. Another instance might be running.")
                    print("   Try running: 'sudo lsof -i :{OSC_SERVER_PORT}' to check")
                    
                if attempt < max_retries - 1:
                    print(f"   Retrying in 2 seconds...")
                    time.sleep(2)
        
        print(f"❌ Failed to start OSC server after {max_retries} attempts")
        return False
    
    def _run_server(self) -> None:
        """Run the OSC server with exception handling."""
        try:
            print("OSC server thread starting...")
            self.osc_server.serve_forever()
            print("OSC server thread exiting normally")
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
        
        # Print debug info for first few messages
        if not was_connected:
            print(f"📡 First data received from remote: y={y}, z={z}, pressure={pressure}")
        
        # Update connection status if changed
        if not was_connected:
            self._update_connection_status(True)
        
        # Parse and store data
        received_data = {"y": y, "z": z, "pressure": bool(pressure)}
        system_state.update_remote_state(received_data)
    
    def _handle_heartbeat(self, unused_addr, value) -> None:
        """Handle heartbeat from remote device."""
        with self.heartbeat_lock:
            self.last_heartbeat_received = time.time()
            was_connected = self.remote_connected
            self.remote_connected = True
        
        # Update connection status if changed
        if not was_connected:
            print("💓 Heartbeat received from remote device")
            self._update_connection_status(True)
    
    def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats to remote device."""
        print("Heartbeat thread started")
        heartbeat_count = 0
        
        while not self.stop_event.is_set():
            if self.osc_client:
                try:
                    self.osc_client.send_message("/heartbeat", [1])
                    heartbeat_count += 1
                    if heartbeat_count % 10 == 0:  # Log every 10 heartbeats
                        print(f"💓 Sent heartbeat #{heartbeat_count}")
                except Exception as e:
                    print(f"Error sending heartbeat: {str(e)}")
            
            # Sleep for heartbeat interval, checking stop flag periodically
            for _ in range(int(HEARTBEAT_INTERVAL * 10)):
                if self.stop_event.is_set():
                    break
                time.sleep(0.1)
    
    def _connection_monitor_loop(self) -> None:
        """Monitor connection status based on heartbeats."""
        print("Connection monitor thread started")
        
        while not self.stop_event.is_set():
            with self.heartbeat_lock:
                current_time = time.time()
                time_since_last_heartbeat = current_time - self.last_heartbeat_received
                
                if self.remote_connected and time_since_last_heartbeat > CONNECTION_TIMEOUT:
                    print(f"⚠️ Remote device connection timeout ({time_since_last_heartbeat:.1f}s since last heartbeat)")
                    self.remote_connected = False
                    self._update_connection_status(False)
                
                # For debugging, periodically print the time since last heartbeat
                if self.last_heartbeat_received > 0 and int(current_time) % 30 == 0:
                    print(f"Time since last heartbeat: {time_since_last_heartbeat:.1f}s")
            
            # Check connection every second
            time.sleep(1)
    
    def _update_connection_status(self, connected: bool) -> None:
        """Update connection status and notify via callback if registered."""
        print(f"Remote device connection: {'✅ Connected' if connected else '❌ Disconnected'}")
        
        # Update system state
        system_state.update_connection_status(connected)
        
        # Notify via callback if registered
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
