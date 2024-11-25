import socket
import cv2
from picamera2 import Picamera2
import pyaudio
import threading
import numpy as np
import sys
import time

# Load the cascade for eye detection
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
STATUS_PORT = 9999   # Port for exchanging overlay status

# Setup sockets
sock_video_front = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock_video_front.bind(("0.0.0.0", VIDEO_PORT_FRONT))

sock_audio = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock_audio.bind(("0.0.0.0", AUDIO_PORT))
sock_audio.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, BUFFER_SIZE)
sock_audio.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, BUFFER_SIZE)

sock_status = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock_status.bind(("0.0.0.0", STATUS_PORT))

# Float array sending setup
FLOAT_ARRAY_PORT = 10004  # Port for sending/receiving float arrays
sock_float_array = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock_float_array.bind(("0.0.0.0", FLOAT_ARRAY_PORT))

# Audio setup
audio = pyaudio.PyAudio()

def initialize_cameras():
    global video_capture_indices
    video_capture_indices = []
    try:
        for i in range(2):  # Attempt to initialize up to 2 cameras
            try:
                picam2 = Picamera2(camera_num=i)
                config = picam2.create_preview_configuration(main={"size": (640, 480)})
                picam2.configure(config)
                picam2.start()
                video_capture_indices.append(picam2)
                print(f"Camera {i} initialized successfully.")
            except Exception as e:
                print(f"Failed to initialize camera {i}: {e}")
    except Exception as e:
        print(f"Error initializing cameras: {e}")
        sys.exit(1)

    if not video_capture_indices:
        print("No cameras detected. Exiting.")
        sys.exit(1)

def receive_overlay_status():
    """Receives overlay status updates from the remote device."""
    global remote_overlay_status
    try:
        while True:
            packet, _ = sock_status.recvfrom(1024)
            remote_overlay_status = bool(int(packet.decode()))
    except Exception as e:
        print(f"Error in receive_overlay_status: {e}")

def set_overlay(status):
    """Sets the overlay status and sends the updated status."""
    global overlay_status
    if overlay_status != status:
        overlay_status = status
        print(f"Overlay status set to: {overlay_status}")
        send_overlay_status()

def send_overlay_status():
    """Sends the current overlay status to the remote device."""
    try:
        status_message = str(int(overlay_status)).encode()
        sock_status.sendto(status_message, (TARGET_IP, STATUS_PORT))
    except Exception as e:
        print(f"Error in send_overlay_status: {e}")

def newEyeDetection():
    """Detects eyes in the video feed and toggles overlay status."""
    global video_capture_indices, framesWithEyes, framesWithEyesLimit, overlay_status
    try:
        cam = video_capture_indices[0]
        frame = cam.capture_array()
        if frame is None:
            return

        # Convert from RGB to BGR
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        resized = cv2.resize(frame, (320, 240))  # Use a lower resolution
        grayscale = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(grayscale, (5, 5), 0)
        eyes = face_cascade.detectMultiScale(blur, 1.2, 6)

        if len(eyes) >= 1:
            set_overlay(True)
            framesWithEyes = 0
        else:
            framesWithEyes += 1
            if framesWithEyes >= framesWithEyesLimit:
                set_overlay(False)

        # Add a short delay to reduce CPU usage
        time.sleep(0.05)
    except Exception as e:
        print(f"Error in newEyeDetection: {e}")

def get_front_camera_stream():
    """Captures video from the front camera and sends it via UDP."""
    global video_capture_indices, current_camera_index, overlay_status, remote_overlay_status
    try:
        while True:
            # Check for eyes
            newEyeDetection()

            if overlay_status and remote_overlay_status:
                current_camera_index = 1 % len(video_capture_indices)
            else:
                current_camera_index = 0

            cap = video_capture_indices[current_camera_index]
            frame = cap.capture_array()
            if frame is None:
                continue

            # Convert from RGB to BGR
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

            # Resize the frame to a smaller resolution (e.g., 320x180)
            frame_resized = cv2.resize(frame, (320, 180))

            # Increase JPEG compression by reducing the quality to 20
            _, buffer = cv2.imencode('.jpg', frame_resized, [int(cv2.IMWRITE_JPEG_QUALITY), 20])

            if len(buffer) < BUFFER_SIZE:
                sock_video_front.sendto(buffer.tobytes(), (TARGET_IP, VIDEO_PORT_FRONT))

            # Limit frame rate to reduce CPU usage
            time.sleep(0.033)  # Approximately 30 frames per second
    except Exception as e:
        print(f"Error in get_front_camera_stream: {e}")

# Initialize cameras and start threads
initialize_cameras()

video_send_thread_front = threading.Thread(target=get_front_camera_stream, daemon=True)
status_receive_thread = threading.Thread(target=receive_overlay_status, daemon=True)

video_send_thread_front.start()
status_receive_thread.start()

# Main loop for commands
try:
    while True:
        print("\nCommands:")
        print("1: Toggle Overlay")
        print("2: Quit")
        command = input("Enter a command: ")
        if command == "1":
            overlay_status = not overlay_status
            send_overlay_status()
            print(f"Overlay status toggled to: {overlay_status}")
        elif command == "2":
            break
except KeyboardInterrupt:
    print("Program interrupted.")
finally:
    for cam in video_capture_indices:
        cam.close()
    sock_video_front.close()
    sock_audio.close()
    sock_status.close()
    sock_float_array.close()
    audio.terminate()
    print("Resources cleaned up. Exiting.")
