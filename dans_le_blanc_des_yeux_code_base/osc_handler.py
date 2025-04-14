"""
OSC communication handler with corrected port configuration to ensure
both devices can properly communicate with each other.
Optimized for bandwidth conservation when idle.
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
LOCAL_SERVER_PORT = 8888           # Port to listen on
REMOTE_SERVER_PORT = 8888          # Port the remote device is listening on (changed from 9999!)

# Heartbeat timing configuration
ACTIVE_HEARTBEAT_INTERVAL = 3.0    # seconds when active
IDLE_HEARTBEAT_INTERVAL = 300.0    # seconds when idle (5 minutes)
RECONNECT_START_INTERVAL = 60.0    # Start with 1 minute when trying to reconnect
RECONNECT_MIN_INTERVAL = 15.0      # Minimum interval when trying to reconnect
RECONNECT_FACTOR = 0.7             # Factor to multiply interval by when trying to reconnect

# Connection timeouts
ACTIVE_CONNECTION_TIMEOUT = 10.0   # seconds when active
IDLE_CONNECTION_TIMEOUT = 600.0    # seconds when idle (10 minutes)

class OSCHandler:
    """Handles OSC communication with dynamic heartbeat timing based on system state."""
    
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
        
        # Heartbeat tracking
        self.current_heartbeat_interval = ACTIVE_HEARTBEAT_INTERVAL
        self.reconnect_attempt_count = 0
        
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
        
        # Activity is detected when sending data
        system_state.update_activity()
        
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
    
    def _start_osc_server(self, max_retries: int = 3) -> bool:
        """Start OSC server with retry mechanism."""
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
            # Reset reconnect attempt count since we received data
            self.reconnect_attempt_count = 0
        
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
            # Reset reconnect attempt count since we received a heartbeat
            self.reconnect_attempt_count = 0
        
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
            # Reset reconnect attempt count
            self.reconnect_attempt_count = 0
            
        if not was_connected:
            self._update_connection_status(True)
    
    def _update_heartbeat_interval(self):
        """Update the heartbeat interval based on system state and connection status."""
        is_idle = system_state.is_idle_mode()
        
        # If we're connected, use the appropriate interval based on idle state
        if self.remote_connected:
            self.reconnect_attempt_count = 0
            if is_idle:
                self.current_heartbeat_interval = IDLE_HEARTBEAT_INTERVAL
            else:
                self.current_heartbeat_interval = ACTIVE_HEARTBEAT_INTERVAL
        else:
            # If not connected, use reconnection strategy with backoff
            if self.reconnect_attempt_count == 0:
                # First reconnection attempt uses the starting interval
                self.current_heartbeat_interval = RECONNECT_START_INTERVAL
            else:
                # Each subsequent attempt reduces the interval (faster heartbeats)
                # but not below the minimum interval
                new_interval = self.current_heartbeat_interval * RECONNECT_FACTOR
                self.current_heartbeat_interval = max(RECONNECT_MIN_INTERVAL, new_interval)
            
            self.reconnect_attempt_count += 1
            
            # Log the current reconnect strategy
            print(f"Reconnect attempt #{self.reconnect_attempt_count}, "
                  f"heartbeat interval: {self.current_heartbeat_interval:.1f}s")
    
    def _connection_manager_loop(self) -> None:
        """Continuously manage client connection."""
        while not self.stop_event.is_set():
            # If we're not connected, try to establish connection
            if not self.remote_connected:
                if self.osc_client is None:
                    print("Attempting to create OSC client...")
                    self._init_osc_client()
                else:
                    # Try to send a heartbeat to establish connection
                    try:
                        print("Sending probe heartbeat...")
                        self.osc_client.send_message("/heartbeat", [1])
                    except Exception as e:
                        # This is expected if remote isn't available
                        pass
            
            # Sleep for a moderate interval between connection attempts
            # We don't want to flood the network but want to be responsive
            for _ in range(50):  # 5 seconds with 100ms checks
                if self.stop_event.is_set():
                    break
                time.sleep(0.1)
    
    def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats to remote device with dynamic interval."""
        print("Heartbeat thread started")
        heartbeat_count = 0
        last_heartbeat_time = 0
        
        while not self.stop_event.is_set():
            current_time = time.time()
            
            # Update the heartbeat interval based on current state
            self._update_heartbeat_interval()
            
            # Send heartbeat if it's time
            if current_time - last_heartbeat_time >= self.current_heartbeat_interval:
                if self.osc_client:
                    try:
                        self.osc_client.send_message("/heartbeat", [1])
                        heartbeat_count += 1
                        last_heartbeat_time = current_time
                        
                        # Only log every few heartbeats, based on mode
                        if system_state.is_idle_mode():
                            # In idle mode, log every heartbeat
                            print(f"💓 Sent heartbeat #{heartbeat_count} (idle mode, interval={self.current_heartbeat_interval:.1f}s)")
                        elif heartbeat_count % 10 == 0:  
                            # In active mode, log every 10 heartbeats
                            print(f"💓 Sent heartbeat #{heartbeat_count} (active mode, interval={self.current_heartbeat_interval:.1f}s)")
                    except Exception as e:
                        # Expected to fail sometimes if remote is down
                        print(f"Failed to send heartbeat: {e}")
            
            # Sleep briefly, checking stop flag frequently
            # This gives us more responsive shutdown
            for _ in range(10):  # Check every 100ms
                if self.stop_event.is_set():
                    break
                time.sleep(0.1)
    
    def _connection_monitor_loop(self) -> None:
        """Monitor connection status based on heartbeats with dynamic timeout."""
        print("Connection monitor thread started")
        
        while not self.stop_event.is_set():
            with self.heartbeat_lock:
                current_time = time.time()
                # Only evaluate timeout if we've received at least one heartbeat
                if self.last_heartbeat_received > 0:
                    time_since_last_heartbeat = current_time - self.last_heartbeat_received
                    
                    # Use different timeout based on idle mode
                    timeout = IDLE_CONNECTION_TIMEOUT if system_state.is_idle_mode() else ACTIVE_CONNECTION_TIMEOUT
                    
                    if self.remote_connected and time_since_last_heartbeat > timeout:
                        print(f"⚠️ Remote device connection timeout ({time_since_last_heartbeat:.1f}s since last heartbeat)")
                        print(f"Connection timeout was {timeout:.1f}s ({['active', 'idle'][system_state.is_idle_mode()]} mode)")
                        self.remote_connected = False
                        self._update_connection_status(False)
                    
                    # For debugging, less frequently when idle
                    elif self.remote_connected and system_state.is_idle_mode() and int(current_time) % 900 == 0:
                        # Every 15 minutes in idle mode
                        print(f"Idle mode: {time_since_last_heartbeat:.1f}s since last heartbeat (timeout={timeout:.1f}s)")
                    elif self.remote_connected and not system_state.is_idle_mode() and int(current_time) % 30 == 0:
                        # Every 30 seconds in active mode
                        print(f"Active mode: {time_since_last_heartbeat:.1f}s since last heartbeat (timeout={timeout:.1f}s)")
            
            # Check connection every second
            time.sleep(1)
    
    def _update_connection_status(self, connected: bool) -> None:
        """Update connection status and notify via callback if registered."""
        if connected:
            print(f"Remote device connection: ✅ Connected")
            self.current_heartbeat_interval = ACTIVE_HEARTBEAT_INTERVAL
            self.reconnect_attempt_count = 0
        else:
            print(f"Remote device connection: ❌ Disconnected")
        
        # Update system state
        system_state.update_connection_status(connected)
        
        # Notify via callback if registered
        if self.connection_status_callback:
            self.connection_status_callback(connected)
    
    def _serial_read_loop(self) -> None:
        """Read data from Arduino and send via OSC, with reduced frequency when idle."""
        last_send_time = 0
        
        while not self.stop_event.is_set():
            if not self.serial_handler.connected:
                time.sleep(1)
                continue
            
            # Get local state
            local_state = system_state.get_local_state()
            moving_state = local_state.get("moving", False)
            
            # Determine if we need to send data based on idle mode
            current_time = time.time()
            should_send = False
            
            if system_state.is_idle_mode():
                # In idle mode, only send data when pressure changes or every ~10 seconds
                should_send = current_time - last_send_time >= 10.0
            else:
                # In active mode, keep higher frequency (~30 Hz)
                should_send = True
            
            # If there's pressure, always send data regardless of idle mode
            if local_state.get("pressure", False):
                should_send = True
            
            if should_send:
                # Request data from Arduino
                response = self.serial_handler.send_command_wait_response(".\n", timeout=1.0)
                if not response:
                    time.sleep(0.1)
                    continue
                
                # Parse the response
                parsed_data = parse_serial_data(response)
                if parsed_data:
                    # Update pressure data even when moving, but don't update orientation
                    if moving_state:
                        # Only update pressure when moving to avoid conflicts
                        pressure_data = {"pressure": parsed_data.get("pressure", False)}
                        system_state.update_local_state(pressure_data)
                        
                        # Don't log continuously when in idle mode
                        if not system_state.is_idle_mode() or current_time - last_send_time >= 10.0:
                            print(f"Updated pressure only: {pressure_data['pressure']} (while moving)")
                    else:
                        # Update all state data when not moving
                        system_state.update_local_state(parsed_data)
                    
                    # Send to remote device if connected
                    if self.remote_connected:
                        self.send_data(parsed_data)
                        last_send_time = current_time
            
            # Brief pause to prevent flooding
            if system_state.is_idle_mode():
                time.sleep(0.1)  # ~10Hz in idle mode
            else:
                time.sleep(0.03)  # ~30Hz in active mode

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
