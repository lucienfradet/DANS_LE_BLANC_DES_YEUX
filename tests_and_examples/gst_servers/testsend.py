import socket
import cv2
from picamera2 import Picamera2
import struct

# Initialize PiCam3
picam = Picamera2()
picam.configure(picam.create_preview_configuration())
picam.start()

# Define the target Pi's Tailscale IP and port
TARGET_IP = "192.168.0.32"  # Replace with the receiving Pi's Tailscale IP
TARGET_PORT = 5000

# Create a TCP socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    print(f"Connecting to {TARGET_IP}:{TARGET_PORT}...")
    s.connect((TARGET_IP, TARGET_PORT))
    print("Connection established!")

    try:
        while True:
            # Capture a frame from the camera
            frame = picam.capture_array()

            # Encode the frame as a JPEG
            _, encoded_image = cv2.imencode('.jpg', frame)

            # Send the size of the frame (as 4 bytes)
            frame_size = len(encoded_image)
            s.sendall(struct.pack(">I", frame_size))

            # Send the actual frame data
            s.sendall(encoded_image.tobytes())
    except KeyboardInterrupt:
        print("Streaming stopped by user.")
    except Exception as e:
        print(f"An error occurred: {e}")
# Calculate the frame size
frame_size = len(encoded_image)

# Send the size of the frame (4 bytes)
s.sendall(struct.pack(">I", frame_size))  # Sends frame size as 4 bytes

# Send the actual frame data
s.sendall(encoded_image)  # Sends the full frame data
