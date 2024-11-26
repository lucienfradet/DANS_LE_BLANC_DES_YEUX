"""
This file does:
- handle Serial data from the Arduino
- send and recieve that data between the two devices
- store the data in an object
- send the MPU (gyro) data of the other device to the arduino
"""

import serial
import socket
import threading
import time
from pythonosc.udp_client import SimpleUDPClient
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import BlockingOSCUDPServer

from shared_variables import config, local_osc, received_osc, update_local_osc, update_recieved_osc
from motor import MotorController

# Hardcoded configuration values
ARDUINO_SERIAL_PORT = "/dev/ttyACM0"
ARDUINO_BAUDRATE = 9600
OSC_IP = config['ip']['pi-ip']
OSC_PORT = 8888

# Initialize serial connections with retry logic
def init_serial_connection(port, baudrate):
    while True:
        try:
            print(f"Attempting to connect to serial port: {port}")
            connection = serial.Serial(port, baudrate, timeout=1)
            print(f"Connected to serial port: {port}")
            return connection
        except serial.SerialException as e:
            print(f"Failed to connect to serial port {port}: {e}. Retrying in 2 seconds...")
            time.sleep(2)

# Initialize serial connections
arduino_serial = init_serial_connection(ARDUINO_SERIAL_PORT, ARDUINO_BAUDRATE)

# OSC client and object storage
osc_client = None
# received_osc = {"y": 0, "z": 0, "pressure": False}
# local_osc = {"y": 0, "z": 0, "pressure": False}

# Initialize OSC client with retry logic
def init_osc_client(ip, port):
    while True:
        try:
            print(f"Attempting to connect to OSC server at {ip}:{port}")
            client = SimpleUDPClient(ip, port)
            print(f"Connected to OSC server at {ip}:{port}")
            return client
        except Exception as e:
            print(f"Failed to connect to OSC server at {ip}:{port}: {e}. Retrying in 2 seconds...")
            time.sleep(2)

osc_client = init_osc_client(OSC_IP, OSC_PORT)

# Function to parse serial input
def parse_serial_line(line):
    try:
        parts = line.strip().split(", ")
        parsed = {}
        for part in parts:
            key, value = part.split(": ")
            if key in ["y", "z"]:
                parsed[key] = int(value)
            elif key == "pressure":
                parsed[key] = value == "1"
        return parsed
    except Exception as e:
        print(f"Error parsing line: {line}, Returning None!\n Error: {e}")
        return None

# Function to read serial data and send via OSC
def read_and_send_serial():
    global local_osc
    global received_osc
    while True:
        try:
            if arduino_serial.in_waiting > 0:
                # request data by sending a dot
                arduino_serial.write(b".") #* encode string to bytes
                line = arduino_serial.readline().decode().strip()
                # print(f"Received from Arduino: {line}")
                data = parse_serial_line(line)
                # update local_osc
                update_local_osc(data)
                if data:
                    # Send parsed data via OSC
                    osc_client.send_message("/data", data)
        except Exception as e:
            print(f"Error in read_and_send_serial: {e}")
            time.sleep(1)

# OSC handler function
def handle_osc_data(unused_addr, y, z, pressure):
    received_osc_temp = {"y": y, "z": z, "pressure": pressure}
    update_recieved_osc(received_osc_temp)
    print(f"Received from OSC: {received_osc}")

# Set up OSC server with retry logic
def start_osc_server():
    while True:
        try:
            dispatcher = Dispatcher()
            dispatcher.map("/data", handle_osc_data)
            server = BlockingOSCUDPServer(("0.0.0.0", OSC_PORT), dispatcher)
            print("OSC Server running...")
            server.serve_forever()
        except Exception as e:
            print(f"Error starting OSC server: {e}. Retrying in 2 seconds...")
            time.sleep(2)

def run_osc_handler():
    motor_controller = MotorController(arduino_serial)
    motor_controller.start()
    threading.Thread(target=read_and_send_serial, daemon=True).start()
    start_osc_server()

if __name__ == "__main__":
    run_osc_handler()
