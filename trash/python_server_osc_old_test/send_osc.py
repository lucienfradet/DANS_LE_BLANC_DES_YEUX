import serial
import time
from pythonosc import udp_client

# Set up the serial connection
arduino_port = '/dev/ttyACM0'  # Update with your Arduino port
baud_rate = 9600
arduino = serial.Serial(arduino_port, baud_rate)
time.sleep(2)  # Give time for the serial connection to initialize

# Set up the OSC client
osc_ip = '100.122.183.98'  # Replace with the IP of your Raspberry Pi
osc_port = 8000  # Replace with your desired OSC port
client = udp_client.SimpleUDPClient(osc_ip, osc_port)

def clamp(value, min_val=-100, max_val=100):
    return max(min_val, min(value, max_val))

while True:
    if arduino.in_waiting > 0:
        try:
            # Read and decode the line from Arduino

            # Clear any extra data left in the serial buffer after reading
            arduino.reset_input_buffer()
            time.sleep(0.05)  # Adjust the delay as needed

            line = arduino.readline().decode('utf-8').strip()
            gx, gy, gz = map(lambda x: int(round(float(x))), line.split(","))
            gy = clamp(gy)

            # Send gyroscope data over OSC
            client.send_message("/gyro", [gx, gy, gz])
            print(f"Sent gyroscope data: gx={gx}, gy={gy}, gz={gz}")
        except ValueError:
            print("Error parsing data")
            print(f"error values gyroscope data: gx={gx}, gy={gy}, gz={gz}")
    time.sleep(3)  # Adjust the delay as needed
