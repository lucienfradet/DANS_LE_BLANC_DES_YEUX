import socket
import cv2
import pyaudio
import threading
import numpy as np
import sys

# Load the cascade, its basically a machine learning algorithm thing for eye detection, dont worry too much about it but you DO need that xml file in the same dir as this script.
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

#float array sending setup
FLOAT_ARRAY_PORT = 10004  # Port for sending/receiving float arrays
sock_float_array = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock_float_array.bind(("0.0.0.0", FLOAT_ARRAY_PORT))

# Audio setup
audio = pyaudio.PyAudio()

# Function to initialize all cameras
def initialize_cameras():
    global video_capture_indices
    for i in range(2):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            video_capture_indices.append(cap)

# Function to capture and send front camera stream
#REWORKED this now sends camera stream based on overlay status values
def get_front_camera_stream():
    global video_capture_indices, current_camera_index, overlay_status, remote_overlay_status
    while True:
            #check for eyes
            newEyeDetection()

            if overlay_status and remote_overlay_status:
                current_camera_index = 1
            else:
                current_camera_index = 0
            
            cap = video_capture_indices[current_camera_index]
            ret, frame = cap.read()
            if not ret:
                continue

            # Resize the frame to a smaller resolution (e.g., 320x180)
            frame_resized = cv2.resize(frame, (320, 180))

            # Increase JPEG compression by reducing the quality to 30
            _, buffer = cv2.imencode('.jpg', frame_resized, [int(cv2.IMWRITE_JPEG_QUALITY), 30])

            if len(buffer) < BUFFER_SIZE:
                sock_video_front.sendto(buffer, (TARGET_IP, VIDEO_PORT_FRONT))

# Function to receive and display the camera streams
def receive_camera_stream():
    global overlay_status, remote_overlay_status
    while True:
        # Receive data from remote device camera
        packet_front, _ = sock_video_front.recvfrom(BUFFER_SIZE) #
        frame_front = cv2.imdecode(np.frombuffer(packet_front, dtype=np.uint8), cv2.IMREAD_COLOR)

        # Ensure valid frames
        if frame_front is None:
            continue

        # Resize frames (to match the reduced resolution for both front and back)
        resized_front = cv2.resize(frame_front, (320, 180))

        cv2.namedWindow("Camera Stream", cv2.WINDOW_FULLSCREEN)
        cv2.setWindowProperty("Camera Stream", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

        if overlay_status and remote_overlay_status:
            local_camera = video_capture_indices[(0) % len(video_capture_indices)]
            ret_front, frame_local = local_camera.read()
            if frame_local is None:
                continue
            resized_local = cv2.resize(frame_local, (320, 180))
            thisOverlay = cv2.addWeighted(resized_front, 0.7, resized_local, 0.3, 0)
            thisOverlayRescaled = cv2.resize(thisOverlay, (1024, 600))
            cv2.imshow("Camera Stream", thisOverlayRescaled)
        else:
            resized_front_rescaled = cv2.resize(resized_front, (1024, 600))
            cv2.imshow("Camera Stream", resized_front_rescaled)
        
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cv2.destroyAllWindows()

# Function to capture audio and send it over UDP
def get_audio_stream():
    """Captures audio from the selected microphone and sends it over UDP."""
    audioIndex = 0
    if overlay_status and remote_overlay_status:
        audioIndex = 1
    stream = audio.open(format=AUDIO_FORMAT,
                        channels=AUDIO_CHANNELS,
                        rate=AUDIO_RATE,
                        input=True,
                        frames_per_buffer=AUDIO_CHUNK,
                        input_device_index=audioIndex)

    while True:
        data = stream.read(AUDIO_CHUNK, exception_on_overflow=False)
        sock_audio.sendto(data, (TARGET_IP, AUDIO_PORT))

# Function to receive audio stream and play it
def receive_audio_stream():
    """Receives audio stream and plays it using PyAudio."""
    stream = audio.open(format=AUDIO_FORMAT,
                        channels=AUDIO_CHANNELS,
                        rate=AUDIO_RATE,
                        output=True)

    while True:
        try:
            packet, _ = sock_video_front.recvfrom(BUFFER_SIZE)
            stream.write(packet)

        except ConnectionResetError as e:
            print(f"Connection was reset: {e}")
    # Handle reconnection logic or clean up resources

        #packet, _ = sock_audio.recvfrom(BUFFER_SIZE) #
        
# Function to list available audio devices (microphones)
def list_audio_devices():
    """Lists all available audio input devices (microphones)."""
    available_devices = []
    for i in range(audio.get_device_count()):
        info = audio.get_device_info_by_index(i)
        if info['maxInputChannels'] > 0:
            available_devices.append(i)
    return available_devices

# Command to switch microphone
def switch_microphone():
    global audio_input_index
    print("Listing available audio devices (microphones):")
    devices = list_audio_devices()
    if devices:
        for i, device in enumerate(devices):
            print(f"{i}. Microphone Index {device}")
        selected_index = int(input("Enter the index of the microphone to switch to: "))
        if 0 <= selected_index < len(devices):
            audio_input_index = devices[selected_index]
            print(f"Switched to Microphone Index {audio_input_index}")
        else:
            print("Invalid microphone index.")
    else:
        print("No microphones detected.")

# Function to send the current overlay status to the other device
def send_overlay_status():
    global overlay_status
    status_message = str(int(overlay_status)).encode()
    sock_status.sendto(status_message, (TARGET_IP, STATUS_PORT))

# Function to receive the overlay status from the other device
def receive_overlay_status():
    global remote_overlay_status
    while True:
        packet, _ = sock_status.recvfrom(1024)
        remote_overlay_status = bool(int(packet.decode()))

# Function to toggle overlay status
def toggle_overlay():
    global overlay_status
    overlay_status = not overlay_status
    print(f"Overlay status: {overlay_status}")
    send_overlay_status()

def set_overlay(bool):
    global overlay_status
    if bool != overlay_status:
        overlay_status = bool
        print(f"Overlay status: {overlay_status}")
        send_overlay_status()

def send_float_array(float_array): #USE THIS TO SEND GYRO DATA AS A FLOAT ARRAY
    """Sends an array of floats to the remote device."""
    # Convert the list of floats to a numpy array
    float_array = [102.27, 38719.232, 8123.782]
    array_np = np.array(float_array, dtype=np.float32)
    
    # Convert the numpy array to bytes
    byte_data = array_np.tobytes()
    
    # Send the byte data via UDP
    sock_float_array.sendto(byte_data, (TARGET_IP, FLOAT_ARRAY_PORT))
   # print(f"Sent float array: {float_array}")

def receive_float_array():
    """Receives an array of floats from the remote device."""
    while True:
        try:
            byte_data, _ = sock_float_array.recvfrom(1024)  # Adjust buffer size as needed
            # Convert bytes back to a numpy array
            float_array = np.frombuffer(byte_data, dtype=np.float32)
            print(f"Received float array: {float_array}")
        except Exception as e:
            print(f"Error receiving float array: {e}")

def newEyeDetection():
    global video_capture_indices, framesWithEyes, framesWithEyesLimit
    cam = video_capture_indices[(1) % len(video_capture_indices)]
    #print("running newEyeDetection")
    #cam = eyeCheckCam
    _, frame = cam.read()
    resized = cv2.resize(frame, (1280, 720)) #might want to rescale to 1920 x 1080 for our 1080p cameras
    grayscale = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(grayscale, (5, 5), 0)
    eyes = face_cascade.detectMultiScale(blur, 1.2, 6)
    eyeCount = len(eyes)
    if eyeCount >= 1:
        set_overlay(True)
        framesWithEyes = 0
    else:
        #print("NO EYES!")
        framesWithEyes += 1
        if framesWithEyes >= framesWithEyesLimit:
            set_overlay(False)

# Initialize cameras and start threads
initialize_cameras()
video_send_thread_front = threading.Thread(target=get_front_camera_stream, daemon=True)
video_receive_thread = threading.Thread(target=receive_camera_stream, daemon=True)
status_receive_thread = threading.Thread(target=receive_overlay_status, daemon=True)

video_send_thread_front.start()
video_receive_thread.start()
status_receive_thread.start()

# Start audio threads
audio_send_thread = threading.Thread(target=get_audio_stream, daemon=True)
audio_receive_thread = threading.Thread(target=receive_audio_stream, daemon=True)
audio_send_thread.start()
audio_receive_thread.start()

#float threads
float_array_receive_thread = threading.Thread(target=receive_float_array, daemon=True)
float_array_receive_thread.start()



# Command loop
while True:
    print("\nCommands:")
    print("1: Toggle Overlay")
    print("2: Quit")
    print("3: list default microphone (switching mics should not do anything)")
    print("4: send float array")
    command = input("Enter a command: ")
    
    match command:
        case "1":
            toggle_overlay()
        case "2":
            break
        case "3":
            switch_microphone()
        case "4":
             # Prompt user to enter a list of floats
           # float_array_input = input("Enter comma-separated float values (e.g., 1.0, 2.5, 3.75): ")
           # float_array = [float(val) for val in float_array_input.split(",")]
            float_array = [123.3, 123.3, 123.3]
            send_float_array(float_array)
        case _:
            print("Invalid command")

# Clean up resources
sock_video_front.close()
sock_audio.close()
sock_status.close()
audio.terminate()
cv2.destroyAllWindows()
