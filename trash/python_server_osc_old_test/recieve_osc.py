from pythonosc import dispatcher, osc_server
import serial
import time

# Set up serial communication with the Arduino
arduino_serial = serial.Serial('/dev/ttyACM0', 9600)  # Replace '/dev/ttyUSB0' with your Arduino's port
time.sleep(2)  # Give some time for the connection to initialize

def gyro_handler(address, *args):
    # Format the gyroscope data as comma-separated values
    print(f"{args[0]},{args[1]},{args[2]}")
    gyro_data = f"{args[0]},{args[1]},{args[2]}"

    # Print the values to the console
    print(gyro_data)

    # Send the formatted data over serial to the Arduino
    arduino_serial.write((gyro_data + "\n").encode())

dispatcher = dispatcher.Dispatcher()
dispatcher.map("/gyro", gyro_handler)

ip = "100.122.183.98"  # Replace with the IP of your Raspberry Pi or localhost
port = 8000  # Use the same port as specified in the sender

server = osc_server.ThreadingOSCUDPServer((ip, port), dispatcher)
print(f"Listening for OSC messages on {ip}:{port}")

try:
    server.serve_forever()
except KeyboardInterrupt:
    print("Shutting down server")
finally:
    arduino_serial.close()
