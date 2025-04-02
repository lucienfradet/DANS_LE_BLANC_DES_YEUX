"""
Enhanced OSC communication handler with improved connection reliability
and smarter retry logic for Dans le Blanc des Yeux installation.
"""

import time
import threading
import socket
import subprocess
from typing import Dict, Any, Optional, Callable, List, Tuple
import random  # For jittered retry

from pythonosc.udp_client import SimpleUDPClient
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

from system_state import system_state
from serial_handler import SerialHandler, parse_serial_data

# Constants
LOCAL_SERVER_PORT = 8888           # Port to listen on
REMOTE_SERVER_PORT = 8888          # Port the remote device is listening on (both use the same port)
HEARTBEAT_INTERVAL = 2.0           # seconds (reduced from 3.0)
CONNECTION_TIMEOUT = 7.0           # seconds (increased from 5.0)
INITIAL_RETRY_INTERVAL = 0.5       # seconds for initial connection attempts
MAX_RETRY_INTERVAL = 5.0           # maximum seconds between retries
STARTUP_GRACE_PERIOD = 15.0        # seconds of more aggressive connection attempts during startup

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
        self.startup_time = time.time()
        
        # Threads
        self.threads = []
        self.running = False
        self.stop_event = threading.Event()
        
        # Callbacks
        self.connection_status_callback = None
        
        # Retry tracking for exponential backoff
        self.connection_attempts = 0
        self.last_attempt_time = 0
        
        # Display network information
        self._display_network_info()
    
    def _display_network_info(self):
        """Display network interface information to help with debugging."""
        print(f"\n--- Network Configuration ---")
        print(f"Remote device IP: {self.remote_ip}")
        
        # Print local network interfaces for debugging
        try:
            if 'posix' in socket.module.__name__:
                cmd = "hostname -I"
            else:
                cmd = "ipconfig"
                
            interfaces = subprocess.check_output(cmd, shell=True).decode('utf-8')
            print(f"Local addresses: {interfaces.strip()}")
        except Exception as e:
            print(f"Could not determine network interfaces: {str(e)}")
            
        print(f"OSC server listening on: 0.0.0.0:{LOCAL_SERVER_PORT}")
        print(f"OSC client sending to: {self.remote_ip}:{REMOTE_SERVER_PORT}")
        print(f"--- End Network Info ---\n")
    
    def start(self) -> bool:
        """Start the OSC handler and all its threads."""
        print("Starting OSC Handler...")
        self.running = True
        self.stop_event.clear()
        
        # FIRST: Start OSC server - this needs to be up to receive connections
        if not self._start_osc_server():
            print("Failed to start OSC server")
            return False
        
        # SECOND: Initialize OSC client to send messages
        if not self._init_osc_client():
            print("Warning: Failed to initialize OSC client, will retry in background")
            # Don't return false - we'll keep trying to connect in the background
        
        # Update system state with initial connection status
        system_state.update_connection_status(False)
        
        # Start background threads for continuous operation
        
        # Start connection manager thread (handles reconnection attempts)
        conn_thread = threading.Thread(target=self._connection_manager_loop)
        conn_thread.daemon = True
        conn_thread.start()
        self.threads.append(conn_thread)
        
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
    
    def send_data(self, data: Dict[str, Any], max_retries: int = 2) -> bool:
        """Send data to remote device with retry mechanism."""
        if not self.osc_client:
            return False
            
        if not self.remote_connected:
            # Don't retry if we know we're disconnected
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
                if attempt == max_retries - 1:
                    print(f"Error sending OSC data: {str(e)}")
                    # Last attempt failed, update connection status
                    self._update_connection_status(False)
                    return False
                # Brief pause between retries
                time.sleep(0.2)
    
    def _init_osc_client(self) -> bool:
        """Initialize OSC client with retry mechanism."""
        try:
            print(f"Initializing OSC client to {self.remote_ip}:{REMOTE_SERVER_PORT}")
            self.osc_client = SimpleUDPClient(self.remote_ip, REMOTE_SERVER_PORT)
            
            # Test connection by sending a heartbeat
            self.osc_client.send_message("/heartbeat", [1])
            print(f"OSC client initialized successfully")
            return True
        except (socket.error, OSError) as e:
            print(f"Failed to initialize OSC client: {str(e)}")
            return False
    
    def _start_osc_server(self, max_retries: int = 5) -> bool:  # Increased from 3 to 5
        """Start OSC server with improved retry mechanism."""
        for attempt in range(max_retries):
            try:
                print(f"Starting OSC server on port {LOCAL_SERVER_PORT} (Attempt {attempt+1}/{max_retries})")
                
                # Create dispatcher
                dispatcher = Dispatcher()
                dispatcher.map("/data", self._handle_data)
                dispatcher.map("/heartbeat", self._handle_heartbeat)
                dispatcher.set_default_handler(self._handle_default)
                
                # Create server with timeout
                self.osc_server = ThreadingOSCUDPServer(
                    ("0.0.0.0", LOCAL_SERVER_PORT), 
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
                
                print(f"✅ OSC server started on port {LOCAL_SERVER_PORT}")
                return True
            except (socket.error, OSError) as e:
                print(f"❌ Failed to start OSC server: {str(e)}")
                # Check if port is already in use
                if "Address already in use" in str(e):
                    print("   Port is already in use. Another instance might be running.")
                    print(f"   Try running: 'sudo lsof -i :{LOCAL_SERVER_PORT}' to check")
                    
                    # Try to kill the process using the port
                    try:
                        if 'posix' in socket.module.__name__:
                            subprocess.call(f"kill $(lsof -t -i:{LOCAL_SERVER_PORT})", shell=True)
                            print("   Attempted to kill process using this port")
                            time.sleep(1)  # Wait for process to die
                    except:
                        pass
                    
                if attempt < max_retries - 1:
                    # Use jittered backoff for retry timing
                    delay = min(1.0 * (2 ** attempt), 5) * (0.5 + 0.5 * random.random())
                    print(f"   Retrying in {delay:.1f} seconds...")
                    time.sleep(delay)
        
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
    
    def _handle_default(self, addr, *args) -> None:
        """Default handler for unexpected messages."""
        print(f"🔍 Received unexpected OSC message: {addr} {args}")
        # Still count as a heartbeat for connection status
        with self.heartbeat_lock:
            self.last_heartbeat_received = time.time()
            was_connected = self.remote_connected
            self.remote_connected = True
            
        if not was_connected:
            self._update_connection_status(True)
    
    def _connection_manager_loop(self) -> None:
        """Continuously manage client connection with exponential backoff."""
        last_attempt_time = 0
        retry_interval = INITIAL_RETRY_INTERVAL
        
        while not self.stop_event.is_set():
            current_time = time.time()
            
            # If we're not connected, try to establish connection
            if not self.remote_connected:
                # Determine if we should try to connect now
                should_connect = False
                
                # During startup, use more aggressive retry timing
                is_startup_phase = (current_time - self.startup_time) < STARTUP_GRACE_PERIOD
                
                if is_startup_phase:
                    # During startup, retry quickly
                    if current_time - last_attempt_time >= INITIAL_RETRY_INTERVAL:
                        should_connect = True
                else:
                    # After startup, use exponential backoff
                    if current_time - last_attempt_time >= retry_interval:
                        should_connect = True
                
                if should_connect:
                    print(f"Attempting connection to remote device ({self.connection_attempts + 1})")
                    last_attempt_time = current_time
                    self.connection_attempts += 1
                    
                    if self.osc_client is None:
                        self._init_osc_client()
                    else:
                        # Try to send a heartbeat to establish connection
                        try:
                            self.osc_client.send_message("/heartbeat", [1])
                        except Exception:
                            # This is expected if remote isn't available
                            pass
                    
                    # Update retry interval with exponential backoff
                    if not is_startup_phase:
                        # Exponential backoff with jitter
                        retry_interval = min(INITIAL_RETRY_INTERVAL * (1.5 ** self.connection_attempts), MAX_RETRY_INTERVAL)
                        # Add jitter (±20%)
                        retry_interval *= (0.8 + 0.4 * random.random())
                    
                    # Log the next retry time
                    print(f"Next connection attempt in {retry_interval:.1f} seconds")
            else:
                # We're connected, reset retry parameters
                self.connection_attempts = 0
                retry_interval = INITIAL_RETRY_INTERVAL
            
            # Sleep for a short interval to check stop flag frequently
            time.sleep(0.1)
    
    def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats to remote device."""
        print("Heartbeat thread started")
        heartbeat_count = 0
        
        while not self.stop_event.is_set():
            if self.osc_client:
                try:
                    # Stagger heartbeats slightly to avoid exact timing overlap
                    jittered_interval = HEARTBEAT_INTERVAL * (0.9 + 0.2 * random.random())
                    
                    self.osc_client.send_message("/heartbeat", [1])
                    heartbeat_count += 1
                    
                    # Log every 10 heartbeats
                    if heartbeat_count % 10 == 0:
                        print(f"💓 Sent heartbeat #{heartbeat_count}")
                except Exception:
                    # Expected to fail sometimes if remote is down
                    pass
            
            # Sleep for heartbeat interval, checking stop flag periodically
            sleep_interval = min(0.1, HEARTBEAT_INTERVAL / 10)
            for _ in range(int(jittered_interval / sleep_interval)):
                if self.stop_event.is_set():
                    break
                time.sleep(sleep_interval)
    
    def _connection_monitor_loop(self) -> None:
        """Monitor connection status based on heartbeats."""
        print("Connection monitor thread started")
        
        while not self.stop_event.is_set():
            with self.heartbeat_lock:
                current_time = time.time()
                # Only evaluate timeout if we've received at least one heartbeat
                if self.last_heartbeat_received > 0:
                    time_since_last_heartbeat = current_time - self.last_heartbeat_received
                    
                    if self.remote_connected and time_since_last_heartbeat > CONNECTION_TIMEOUT:
                        print(f"⚠️ Remote device connection timeout ({time_since_last_heartbeat:.1f}s since last heartbeat)")
                        self.remote_connected = False
                        self._update_connection_status(False)
                    
                    # For debugging, periodically print the time since last heartbeat
                    elif self.remote_connected and int(current_time) % 30 == 0:
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
        serial_retry_interval = 1.0  # Initial retry interval
        max_serial_retry_interval = 5.0
        serial_retries = 0
        
        while not self.stop_event.is_set():
            if not self.serial_handler.connected:
                # Use exponential backoff for serial retries too
                sleep_time = min(serial_retry_interval * (1.5 ** serial_retries), max_serial_retry_interval)
                serial_retries += 1
                time.sleep(sleep_time)
                continue
            else:
                # Reset retry counter when connected
                serial_retries = 0
            
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
                
                # Send to remote device if connected
                if self.remote_connected:
                    self.send_data(parsed_data)
            
            # Brief pause to prevent flooding
            time.sleep(0.03)  # ~30Hz


def run_osc_handler(remote_ip: str, arduino_port: str = "/dev/ttyACM0") -> Tuple[OSCHandler, SerialHandler]:
    """Initialize and run the OSC handler with given configuration."""
    # Try multiple Arduino ports in case the default one doesn't work
    arduino_ports = [arduino_port, "/dev/ttyACM1", "/dev/ttyUSB0", "/dev/ttyUSB1"]
    
    serial_handler = None
    for port in arduino_ports:
        try:
            print(f"Trying Arduino port: {port}")
            serial_handler = SerialHandler(port=port, baudrate=9600)
            if serial_handler.connect():
                print(f"Connected to Arduino on port {port}")
                break
        except Exception as e:
            print(f"Failed to connect on port {port}: {e}")
    
    if serial_handler is None or not serial_handler.connected:
        print("Warning: Failed to connect to Arduino on any port. Continuing anyway...")
        serial_handler = SerialHandler(port=arduino_port, baudrate=9600)  # Use default port as fallback
    
    # Initialize OSC handler
    osc_handler = OSCHandler(remote_ip, serial_handler)
    osc_handler.start()
    
    return osc_handler, serial_handler
