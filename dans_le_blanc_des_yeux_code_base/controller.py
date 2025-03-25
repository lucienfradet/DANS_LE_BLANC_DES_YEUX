"""
Main controller for the Dans le Blanc des Yeux installation.
Manages all components with proper error handling and cleanup.

Usage:
    python controller.py [--visualize]
"""

import configparser
import time
import signal
import sys
import argparse
from osc_handler import run_osc_handler

def signal_handler(sig, frame):
    """Handle shutdown signals gracefully."""
    print("\nShutting down... Please wait.")
    if 'osc_handler' in globals():
        osc_handler.stop()
    if 'serial_handler' in globals():
        serial_handler.disconnect()
    if 'visualizer' in globals() and visualizer is not None:
        visualizer.stop()
    print("Shutdown complete.")
    sys.exit(0)

if __name__ == "__main__":
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Dans le Blanc des Yeux Controller')
    parser.add_argument('--visualize', action='store_true', help='Enable terminal visualization')
    args = parser.parse_args()
    
    # Initialize visualizer variable
    visualizer = None
    
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Load configuration
    config = configparser.ConfigParser()
    config.read('config.ini')
    
    try:
        # Get remote IP from config
        remote_ip = config['ip']['pi-ip']
        print(f"Using remote IP: {remote_ip}")
        
        # Start visualizer if requested
        if args.visualize:
            from debug_visualizer import run_visualizer
            print("Starting terminal visualizer...")
            visualizer = run_visualizer()
        
        # Start OSC handler
        osc_handler, serial_handler = run_osc_handler(remote_ip)
        
        # Keep main thread alive
        print("System running. Press Ctrl+C to exit.")
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\nUser requested shutdown.")
        signal_handler(None, None)
    except Exception as e:
        print(f"Fatal error: {str(e)}")
        try:
            # Try to clean up
            signal_handler(None, None)
        except:
            sys.exit(1)
