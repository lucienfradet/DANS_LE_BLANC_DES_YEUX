"""
OSC communication handler with improved idle mode for bandwidth optimization.
Implements adaptive heartbeat frequency based on system state.
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
REMOTE_SERVER_PORT = 8888          # Port the remote device is listening on
ACTIVE_HEARTBEAT_INTERVAL = 3.0    # seconds when active
IDLE_HEARTBEAT_INTERVAL = 300.0    # 5 minutes when idle
RECONNECT_HEARTBEAT_INTERVAL = 30.0  # 30 seconds when trying to reconnect
CONNECTION_TIMEOUT = 10.0          # seconds
IDLE_TIMEOUT = 30.0                # seconds of no pressure before entering idle mode

class OSCHandler:
    """Thread-safe OSC handler with bandwidth-optimized idle mode."""
    
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
        
        # Bandwidth optimization - idle mode
        self.is_idle_mode = True  # Start in idle mode by default
        self.heartbeat_interval = IDLE_HEARTBEAT_INTERVAL  # Start with idle interval
        self.last_active_time = 0  # Track when last activity was seen
        self.last_mode_change_time = 0  # Track when mode was last changed
        self.idle_mode_lock = threading.Lock()
        
        # Threads
        self.threads = []
        self.running = False
        self.stop_event = threading.Event()
        
        # Callbacks
        self.connection_status_callback = None
        
        # Serial data polling rate control
        self.active_serial_poll_rate = 0.03  # ~30Hz when active
        self.idle_serial_poll_rate = 1.0     # 1 second when idle
        self.current_serial_poll_rate = self.idle_serial_poll_rate  # Start in idle
        
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
        
        # Set initial state
        with self.idle_mode_lock:
            self.is_idle_mode = True
            self.heartbeat_interval = IDLE_HEARTBEAT_INTERVAL
            self.last_active_time = time.time()
            self.last_mode_change_time = time.time()
            print("Starting in bandwidth-saving idle mode")
        
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
        
        # Start idle mode manager thread
        idle_thread = threading.Thread(target=self._idle_mode_manager_loop)
        idle_thread.daemon = True
        idle_thread.start()
        self.threads.append(idle_thread)
        
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
        
        # Update active time when sending data
        self._update_active_time()
        
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
                dispatcher.map("/wakeup", self._handle_wakeup)  # New wake-up message
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
        
        # Check for pressure state to exit idle mode if needed
        if pressure:
            self._exit_idle_mode("remote device pressure detected")
        
        # Update active time regardless of pressure state
        self._update_active_time()
        
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
    
    def _handle_wakeup(self, unused_addr, value) -> None:
        """Handle wake-up message from remote device."""
        with self.heartbeat_lock:
            self.last_heartbeat_received = time.time()
            was_connected = self.remote_connected
            self.remote_connected = True
        
        # Update connection status if changed
        if not was_connected:
            self._update_connection_status(True)
        
        # Always exit idle mode on explicit wake-up
        self._exit_idle_mode("received wake-up signal from remote")
    
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
        
        # Update active time since we received a message
        self._update_active_time()
    
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
                        # When trying to reconnect, we send wake-up signal
                        print("Sending reconnection wake-up signal...")
                        self.osc_client.send_message("/wakeup", [1])
                    except Exception as e:
                        # This is expected if remote isn't available
                        pass
                    
                    # Switch to more frequent heartbeats when disconnected
                    with self.idle_mode_lock:
                        if self.heartbeat_interval == IDLE_HEARTBEAT_INTERVAL:
                            print("Lost connection while in idle mode, increasing heartbeat frequency for reconnection")
                            self.heartbeat_interval = RECONNECT_HEARTBEAT_INTERVAL
            
            # Sleep for a moderate interval between connection attempts
            # We don't want to flood the network but want to be responsive
            for _ in range(50):  # 5 seconds with 100ms checks
                if self.stop_event.is_set():
                    break
                time.sleep(0.1)
    
    def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats to remote device with adaptive frequency."""
        print("Heartbeat thread started in adaptive mode")
        heartbeat_count = 0
        last_heartbeat_time = 0
        
        while not self.stop_event.is_set():
            current_time = time.time()
            
            # Get current heartbeat interval (thread-safe)
            current_interval = self.heartbeat_interval
            
            # Check if it's time to send a heartbeat
            if current_time - last_heartbeat_time >= current_interval:
                if self.osc_client:
                    try:
                        # Include current mode in heartbeat for debugging
                        mode_value = 0 if self.is_idle_mode else 1
                        self.osc_client.send_message("/heartbeat", [mode_value])
                        last_heartbeat_time = current_time
                        heartbeat_count += 1
                        
                        # Log mode-specific heartbeat
                        if self.is_idle_mode:
                            if heartbeat_count % 2 == 0:  # Log every other idle heartbeat (they're infrequent)
                                print(f"💤 Sent idle mode heartbeat #{heartbeat_count}")
                        else:
                            if heartbeat_count % 20 == 0:  # Log every 20th active heartbeat (they're frequent)
                                print(f"💓 Sent active mode heartbeat #{heartbeat_count}")
                    except Exception:
                        # Expected to fail sometimes if remote is down
                        pass
            
            # Sleep for short interval to check stop flag periodically
            time.sleep(0.5)
    
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
                    # but only in active mode to avoid log spam
                    elif self.remote_connected and not self.is_idle_mode and int(current_time) % 60 == 0:
                        print(f"Time since last heartbeat: {time_since_last_heartbeat:.1f}s")
            
            # Check connection every second
            time.sleep(1)
    
    def _idle_mode_manager_loop(self) -> None:
        """Manage transitions between idle and active modes."""
        print("Idle mode manager thread started")
        
        while not self.stop_event.is_set():
            current_time = time.time()
            
            # Check for idle timeout
            if not self.is_idle_mode:
                # Get both local and remote pressure states
                local_state = system_state.get_local_state()
                remote_state = system_state.get_remote_state()
                local_pressure = local_state.get("pressure", False)
                remote_pressure = remote_state.get("pressure", False)
                
                # If no pressure on either device and we've been inactive for enough time
                if (not local_pressure and not remote_pressure and 
                    current_time - self.last_active_time >= IDLE_TIMEOUT):
                    # Switch to idle mode
                    with self.idle_mode_lock:
                        if not self.is_idle_mode:
                            self.is_idle_mode = True
                            self.heartbeat_interval = IDLE_HEARTBEAT_INTERVAL
                            self.current_serial_poll_rate = self.idle_serial_poll_rate
                            self.last_mode_change_time = current_time
                            print(f"📉 Switching to bandwidth-saving idle mode after {IDLE_TIMEOUT:.1f}s of inactivity")
                            
                            # Update system state with idle status
                            system_state.update_idle_state(True)
            
            # Sleep for one second between checks
            time.sleep(1)
    
    def _update_connection_status(self, connected: bool) -> None:
        """Update connection status and notify via callback if registered."""
        print(f"Remote device connection: {'✅ Connected' if connected else '❌ Disconnected'}")
        
        # Update system state
        system_state.update_connection_status(connected)
        
        # Notify via callback if registered
        if self.connection_status_callback:
            self.connection_status_callback(connected)
        
        # If connection is restored, update active time
        if connected:
            self._update_active_time()
    
    def _update_active_time(self) -> None:
        """Update the timestamp of last activity."""
        with self.idle_mode_lock:
            self.last_active_time = time.time()
    
    def _exit_idle_mode(self, reason: str) -> None:
        """Exit idle mode if currently in it."""
        with self.idle_mode_lock:
            if self.is_idle_mode:
                current_time = time.time()
                self.is_idle_mode = False
                self.heartbeat_interval = ACTIVE_HEARTBEAT_INTERVAL
                self.current_serial_poll_rate = self.active_serial_poll_rate
                self.last_active_time = current_time
                self.last_mode_change_time = current_time
                print(f"📈 Exiting idle mode: {reason}")
                
                # If connected, send wake-up signal to other device
                if self.remote_connected and self.osc_client:
                    try:
                        self.osc_client.send_message("/wakeup", [1])
                        print("Sent wake-up signal to remote device")
                    except Exception as e:
                        print(f"Failed to send wake-up signal: {e}")
                
                # Update system state with active status
                system_state.update_idle_state(False)
    
    def _serial_read_loop(self) -> None:
        """Read data from Arduino and send via OSC with adaptive polling rate."""
        print("Starting serial read loop with adaptive polling rate")
        
        while not self.stop_event.is_set():
            if not self.serial_handler.connected:
                time.sleep(1)
                continue
            
            # Get local state (but we'll still read data even when moving)
            local_state = system_state.get_local_state()
            moving_state = local_state.get("moving", False)
            
            # Request data from Arduino (regardless of moving state)
            response = self.serial_handler.send_command_wait_response(".\n", timeout=1.0)
            if not response:
                time.sleep(0.1)
                continue
            
            # Parse the response
            parsed_data = parse_serial_data(response)
            if parsed_data:
                # Check for pressure state change
                new_pressure = parsed_data.get("pressure", False)
                current_pressure = local_state.get("pressure", False)
                
                # If pressure just became true, exit idle mode
                if new_pressure and not current_pressure:
                    self._exit_idle_mode("local pressure detected")
                
                # Update pressure data even when moving, but don't update orientation
                if moving_state:
                    # Only update pressure when moving to avoid conflicts
                    pressure_data = {"pressure": new_pressure}
                    system_state.update_local_state(pressure_data)
                    
                    # Only log if this changed the pressure state
                    if new_pressure != current_pressure:
                        print(f"Updated pressure only: {pressure_data['pressure']} (while moving)")
                else:
                    # Update all state data when not moving
                    system_state.update_local_state(parsed_data)
                
                # If pressure is true, update active time
                if new_pressure:
                    self._update_active_time()
                
                # Send to remote device if connected
                # In idle mode, only send data if the pressure state changed or became true
                send_data = False
                
                if self.remote_connected:
                    if self.is_idle_mode:
                        # In idle mode, only send when pressure state changes or is true
                        if new_pressure or new_pressure != current_pressure:
                            send_data = True
                    else:
                        # In active mode, always send
                        send_data = True
                
                if send_data:
                    self.send_data(parsed_data)
            
            # Get current poll rate (thread-safe)
            current_poll_rate = self.current_serial_poll_rate
            
            # Brief pause to prevent flooding - use adaptive rate
            time.sleep(current_poll_rate)


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
