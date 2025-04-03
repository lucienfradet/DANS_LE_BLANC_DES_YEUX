"""
Terminal-based visualization for Dans le Blanc des Yeux installation.
Designed to work over SSH connections without requiring a GUI.
"""

import time
import threading
import math
import os
from typing import Dict, Any, List

from system_state import system_state

class TerminalVisualizer:
    def __init__(self):
        self.running = False
        self.thread = None
        self.width = 80
        self.height = 24
        
        # Track last movement commands
        self.last_motor_command = {"y": 0, "z": 0, "timestamp": 0}
        
        # Register as observer for state changes
        system_state.add_observer(self._on_state_change)
        
        # State tracking for efficient updates
        self.needs_redraw = True
    
    def start(self):
        """Start the visualization in a separate thread."""
        if self.thread is not None and self.thread.is_alive():
            print("Visualization already running")
            return self
        
        # Clear screen before starting
        os.system('clear' if os.name == 'posix' else 'cls')
            
        self.running = True
        self.thread = threading.Thread(target=self._display_loop)
        self.thread.daemon = True
        self.thread.start()
        return self
    
    def stop(self):
        """Stop the visualization."""
        self.running = False
        if self.thread is not None:
            self.thread.join(timeout=1.0)
            self.thread = None  # Reset thread to None to allow restart
        
        # Clear screen on exit
        os.system('clear' if os.name == 'posix' else 'cls')
    
    def _on_state_change(self, changed_state):
        """Handle state changes."""
        self.needs_redraw = True
        
        # Check if this is a motor movement update
        if changed_state == "motor_command":
            motor_cmd = system_state.get_last_motor_command()
            if motor_cmd:
                self.last_motor_command = motor_cmd.copy()
    
    def _display_loop(self):
        """Main display loop."""
        try:
            while self.running:
                if self.needs_redraw:
                    self._draw_screen()
                    self.needs_redraw = False
                
                # Wait a bit before next update
                time.sleep(0.5)
                
                # Force redraw every 2 seconds
                self.needs_redraw = True
        except Exception as e:
            print(f"Display error: {str(e)}")
    
    def _draw_screen(self):
        """Draw the main screen with system status."""
        # Get current states
        local_state = system_state.get_local_state()
        remote_state = system_state.get_remote_state()
        
        # Clear the screen
        os.system('clear' if os.name == 'posix' else 'cls')
        
        # Title and header
        print("=" * self.width)
        print("DANS LE BLANC DES YEUX - SYSTEM STATUS".center(self.width))
        print("=" * self.width)
        print()
        
        # Split screen for local and remote
        half_width = self.width // 2 - 2
        
        # Create top row headers
        local_header = "LOCAL DEVICE".center(half_width)
        remote_header = "REMOTE DEVICE".center(half_width)
        print(f"{local_header} | {remote_header}")
        print(f"{'-' * half_width}-+-{'-' * half_width}")
        
        # Format device info
        local_info = self._format_device_info(local_state)
        remote_info = self._format_device_info(remote_state)
        
        # Ensure both info blocks have the same number of lines
        max_lines = max(len(local_info), len(remote_info))
        while len(local_info) < max_lines:
            local_info.append(" " * half_width)
        while len(remote_info) < max_lines:
            remote_info.append(" " * half_width)
        
        # Print side by side
        for i in range(max_lines):
            print(f"{local_info[i]} | {remote_info[i]}")
        
        print()
        print("-" * self.width)
        
        # Draw orientation visualizations
        local_viz = self._create_orientation_viz(local_state.get('y', 0), local_state.get('z', 0))
        remote_viz = self._create_orientation_viz(remote_state.get('y', 0), remote_state.get('z', 0))
        
        print("ORIENTATION VISUALIZATION".center(self.width))
        print()
        
        # Labels
        print(f"{'LOCAL':^{half_width}} | {'REMOTE':^{half_width}}")
        
        # Print visualizations side by side
        for i in range(len(local_viz)):
            print(f"{local_viz[i]} | {remote_viz[i]}")
        
        # Motor Commands
        print()
        print("-" * self.width)
        print("MOTOR COMMANDS".center(self.width))
        
        if self.last_motor_command["timestamp"] > 0:
            elapsed = time.time() - self.last_motor_command["timestamp"]
            if elapsed < 10:  # Only show recently sent commands
                print(f"Last Command: Y={self.last_motor_command['y']}°, Z={self.last_motor_command['z']}° ({elapsed:.1f}s ago)".center(self.width))
            else:
                print("No recent motor commands".center(self.width))
        else:
            print("No motor commands sent yet".center(self.width))

        # Connection status
        print()
        print("-" * self.width)
        
        # Print connection status
        status = "CONNECTED" if remote_state.get('connected', False) else "DISCONNECTED"
        color = "\033[92m" if status == "CONNECTED" else "\033[91m"  # Green or Red
        print(f"Connection Status: {color}{status}\033[0m".center(self.width))
        
        # System instructions
        print()
        print("Press Ctrl+C or q+Enter to exit, v+Enter to toggle visualizer".center(self.width))
    
    def _format_device_info(self, state: Dict[str, Any]) -> List[str]:
        """Format device state information as a list of lines."""
        lines = []
        half_width = self.width // 2 - 2
        
        # Y and Z orientation
        y_val = state.get('y', 0)
        z_val = state.get('z', 0)
        lines.append(f"Y Axis (Tilt): {y_val:3d}°".ljust(half_width))
        lines.append(f"Z Axis (Pan): {z_val:3d}°".ljust(half_width))
        
        # Pressure status with color
        pressure = state.get('pressure', False)
        pressure_text = "DETECTED" if pressure else "NONE"
        pressure_color = "\033[91m" if pressure else "\033[92m"  # Red or Green
        lines.append(f"Pressure: {pressure_color}{pressure_text}\033[0m".ljust(half_width + 10))
        
        # Moving status with color
        moving = state.get('moving', False)
        moving_text = "ACTIVE" if moving else "IDLE"
        moving_color = "\033[93m" if moving else "\033[96m"  # Yellow or Cyan
        lines.append(f"Motors: {moving_color}{moving_text}\033[0m".ljust(half_width + 10))
        
        # Add audio status if available
        if 'audio' in state:
            audio = state.get('audio', {})
            
            # Audio playback status
            playing = audio.get('playing', False)
            playing_text = "ACTIVE" if playing else "MUTED"
            playing_color = "\033[92m" if playing else "\033[90m"  # Green or Gray
            lines.append(f"Audio: {playing_color}{playing_text}\033[0m".ljust(half_width + 10))
            
            # Muted channels
            muted = audio.get('muted_channels', [])
            muted_text = ", ".join(muted) if muted else "NONE"
            lines.append(f"Muted: {muted_text}".ljust(half_width))
            
            # Streaming mic
            streaming_mic = audio.get('streaming_mic', 'none')
            if streaming_mic != 'none':
                streaming_color = "\033[92m"  # Green
                lines.append(f"Streaming: {streaming_color}{streaming_mic}\033[0m Mic".ljust(half_width + 10))
            else:
                streaming_color = "\033[90m"  # Gray
                lines.append(f"Streaming: {streaming_color}OFF\033[0m".ljust(half_width + 10))
        
        return lines

    def _create_orientation_viz(self, y_angle: int, z_angle: int) -> List[str]:
        """Create ASCII art representation of orientation."""
        # Constants for visualization
        width = self.width // 2 - 4
        height = 7
        viz = [" " * width for _ in range(height)]
        
        # Create a box
        viz[0] = "+" + "-" * (width - 2) + "+"
        viz[height-1] = "+" + "-" * (width - 2) + "+"
        
        for i in range(1, height-1):
            viz[i] = "|" + " " * (width - 2) + "|"
        
        # Calculate position for the indicator based on angles
        center_x = width // 2
        center_y = height // 2
        
        # Limit maximum offset
        max_offset_x = (width - 4) // 2
        max_offset_y = (height - 2) // 2
        
        # Calculate offsets based on angles (with scaling)
        x_offset = int(math.sin(math.radians(z_angle)) * max_offset_x)
        y_offset = int(-math.sin(math.radians(y_angle)) * max_offset_y)
        
        # Place indicator (ensure within bounds)
        indicator_x = min(max(1, center_x + x_offset), width - 2)
        indicator_y = min(max(1, center_y + y_offset), height - 2)
        
        # Create a list of characters for the row
        row_chars = list(viz[indicator_y])
        row_chars[indicator_x] = "O"
        viz[indicator_y] = "".join(row_chars)
        
        # Place center reference
        row_chars = list(viz[center_y])
        row_chars[center_x] = "+"
        viz[center_y] = "".join(row_chars)
        
        # Add angle values at the bottom
        viz.append(f"Y:{y_angle:3d}° Z:{z_angle:3d}°".center(width))
        
        return viz


def run_visualizer():
    """Run the terminal visualizer."""
    visualizer = TerminalVisualizer()
    return visualizer.start()


if __name__ == "__main__":
    # Test the visualizer independently
    visualizer = run_visualizer()
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        visualizer.stop()
        print("Visualizer stopped")
