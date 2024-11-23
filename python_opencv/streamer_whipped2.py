import socket
import cv2
from picamera2 import Picamera2
import threading
import numpy as np
import sys
import pyaudio

# Load the cascade, used for eye detection
face_cascade = cv2.CascadeClassifier('haarcascade_eye.xml')
framesWithEyes = 0
framesWithEyesLimit = 20

# Constants
BUFFER_SIZE = 65535
AUDIO_RATE = 44100
AUDIO_CHUNK = 1024
AUDIO_FORMAT = pyaudio.paInt16
AUDIO_CHANNELS = 1

# Default device indices
video_capture_indices = []
audio_input_index = 0
current_camera_index = 0

# Overlay status for both devices
overlay_status = False  # Local overlay status
remote_overlay_status = False  # Remote device's overlay status

# Get target IP and ports from command-line arguments
if len(sys.argv) < 3:
    print("Usage: python script.py <target_ip> <video_port>")
    sys.exit(1)

TARGET_IP = sys.argv[1]
VIDEO_PORT_FRONT = int(sys.argv[2])  # Front camera port
AUDIO_PORT = 10003  # Port for audio stream
STATUS_PORT = 9999  # Port for exchanging overlay status
FLOAT_ARRAY_PORT = 10004  # Port for sending/receiving float arrays

# Setup sockets
sock_video_front = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock_video_front.bind(("0.0.0.0", VIDEO_PORT_FRONT))

sock_audio = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock_audio.bind(("0.0.0.0", AUDIO_PORT))
sock_audio.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, BUFFER_SIZE)
sock_audio.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, BUFFER_SIZE)

sock_status = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock_status.bind(("0.0.0.0", STATUS_PORT))

sock_float_array = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock_float_array.bind(("0.0.0.0", FLOAT_ARRAY_PORT))

# Store the latest received frame
latest_frame = None
frame_lock = threading.Lock()  # Lock for thread-safe access to the frame


def initialize_cameras():
    global video_capture_indices
    video_capture_indices = []
    for i in range(1): #was range(2)
        try:
            picam2 = Picamera2(camera_num=i)
            # Configure the camera (adjust resolution and format as needed)
            picam2.configure(picam2.create_preview_configuration(main={"size": (1920, 1080)}))
            picam2.start()
            video_capture_indices.append(picam2)
            print(f"Camera {i} initialized successfully.")
        except Exception as e:
            print(f"Failed to initialize camera {i}: {e}")


def receive_camera_stream():
    """Receives video stream and updates the latest frame."""
    global latest_frame
    while True:
        # Receive data from remote device camera
        packet_front, _ = sock_video_front.recvfrom(BUFFER_SIZE)
        frame_front = cv2.imdecode(np.frombuffer(packet_front, dtype=np.uint8), cv2.IMREAD_COLOR)

        if frame_front is None:
            continue

        # Update the latest frame
        with frame_lock:
            latest_frame = frame_front


def display_next_frame():
    """Displays the next available frame."""
    global latest_frame
    with frame_lock:
        if latest_frame is not None:
            # Resize the frame for display
            resized_frame = cv2.resize(latest_frame, (1024, 600))
            cv2.imshow("Camera Stream", resized_frame)
            cv2.waitKey(1)
        else:
            print("No frame available to display.")


# Function to toggle overlay status
def toggle_overlay():
    global overlay_status
    overlay_status = not overlay_status
    print(f"Overlay status: {overlay_status}")
    send_overlay_status()


def send_overlay_status():
    """Sends the current overlay status to the remote device."""
    try:
        status_message = str(int(overlay_status)).encode()
        sock_status.sendto(status_message, (TARGET_IP, STATUS_PORT))
    except Exception as e:
        print(f"Error in send_overlay_status: {e}")


# Start threads
initialize_cameras()
video_receive_thread = threading.Thread(target=receive_camera_stream, daemon=True)
video_receive_thread.start()

# Main command loop
try:
    while True:
        print("\nCommands:")
        print("1: Display Next Frame")
        print("2: Toggle Overlay")
        print("3: Quit")
        command = input("Enter a command: ")
        if command == "1":
            display_next_frame()
        elif command == "2":
            toggle_overlay()
        elif command == "3":
            break
        else:
            print("Invalid command.")
except KeyboardInterrupt:
    print("Program interrupted.")
finally:
    # Clean up resources
    sock_video_front.close()
    sock_audio.close()
    sock_status.close()
    sock_float_array.close()
    cv2.destroyAllWindows()
