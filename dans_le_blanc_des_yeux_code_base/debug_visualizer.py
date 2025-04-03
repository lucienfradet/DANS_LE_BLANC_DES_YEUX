"""
Terminal-based visualization for Dans le Blanc des Yeux installation.
Designed to work over SSH connections without requiring a GUI.
Enhanced with audio state visualization.
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
        
        # Audio visualization data
        self.audio_state = {
            "mode": "none",              # Current audio playback mode
            "left_level": 0,             # Left channel audio level (0-10)
            "right_level": 0,            # Right channel audio level (0-10)
            "last_update": 0             # Last time audio levels were updated
        }
        
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
        
        # Check if this is an audio state update
        elif changed_state == "audio_state":
            audio_state = system_state.get_audio_state()
            if audio_state:
                self.audio_state = audio_state.copy()
    
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
        
        # Audio State Visualization
        print()
        print("-" * self.width)
        print("AUDIO STATE".center(self.width))
        
        # Audio playback mode
        self._print_audio_mode()
        
        # Audio level meters
        self._print_audio_levels()
        
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
    
    def _print_audio_mode(self):
        """Print the current audio playback mode."""
        mode = self.audio_state.get("mode", "none")
        mode_desc = ""
        mode_color = "\033[97m"  # Default to white
        
        if mode == "none":
            mode_desc = "No Audio Playing"
            mode_color = "\033[90m"  # Gray
        elif mode == "personal_speaker_right_muted":
            mode_desc = "Playing Personal Mic (Right Channel Muted)"
            mode_color = "\033[94m"  # Blue
        elif mode == "personal_speaker_left_muted":
            mode_desc = "Playing Personal Mic (Left Channel Muted)"
            mode_color = "\033[94m"  # Blue
        elif mode == "global_speaker_right_muted":
            mode_desc = "Playing Global Mic (Right Channel Muted)"
            mode_color = "\033[92m"  # Green
        elif mode == "global_speaker_left_muted":
            mode_desc = "Playing Global Mic (Left Channel Muted)"
            mode_color = "\033[92m"  # Green
        
        print(f"Mode: {mode_color}{mode_desc}\033[0m".center(self.width))
        
        # Describe audio rules based on pressure state
        local_state = system_state.get_local_state()
        remote_state = system_state.get_remote_state()
        local_pressure = local_state.get("pressure", False)
        remote_pressure = remote_state.get("pressure", False)
        
        if not local_pressure and not remote_pressure:
            rule = "No playback when neither device has pressure"
        elif local_pressure and not remote_pressure:
            rule = "Personal mic with muted channel when local has pressure"
        elif not local_pressure and remote_pressure:
            rule = "Global mic with muted channel when remote has pressure"
        else:  # both have pressure
            rule = "Personal mic with muted channel when both have pressure"
            
        print(f"({rule})".center(self.width))
        print()
    
    def _print_audio_levels(self):
        """Print audio level meter visualization."""
        # Get current levels
        left_level = self.audio_state.get("left_level", 0)
        right_level = self.audio_state.get("right_level", 0)
        
        # Update decay effect - reduce levels over time if not updated recently
        current_time = time.time()
        time_since_update = current_time - self.audio_state.get("last_update", 0)
        if time_since_update > 0.1:  # Apply decay after 100ms
            decay_amount = int(time_since_update * 3)  # Decay 3 units per second
            left_level = max(0, left_level - decay_amount)
            right_level = max(0, right_level - decay_amount)
        
        # Define meter width (half the terminal width)
        meter_width = self.width - 20  # Allow for labels
        
        # Create ASCII meter
        left_fill = int((left_level / 10) * meter_width)
        right_fill = int((right_level / 10) * meter_width)
        
        # Add color gradient based on level
        def color_for_level(level, pos):
            if level < 3:
                return "\033[92m"  # Green for low levels
            elif level < 7:
                return "\033[93m"  # Yellow for medium levels
            else:
                return "\033[91m"  # Red for high levels
        
        # Print left channel meter
        left_meter = ""
        for i in range(meter_width):
            if i < left_fill:
                color = color_for_level(left_level, i)
                left_meter += f"{color}█\033[0m"
            else:
                left_meter += "░"
                
        # Print right channel meter
        right_meter = ""
        for i in range(meter_width):
            if i < right_fill:
                color = color_for_level(right_level, i)
                right_meter += f"{color}█\033[0m"
            else:
                right_meter += "░"
        
        # Display meters with labels
        print(f"Left  : {left_meter}")
        print(f"Right : {right_meter}")


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
