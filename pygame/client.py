# client.py
import cv2
import socket
import threading
import pickle
import struct
import pyaudio

def video_stream(client_socket):
    vid = cv2.VideoCapture(0)

    while vid.isOpened():
        ret, frame = vid.read()
        if not ret:
            break
        frame = cv2.resize(frame, (1024, 600))
        encoded, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        data = pickle.dumps(buffer)
        message_size = struct.pack("L", len(data))
        message_type = struct.pack("B", 1)  # Message type 1 for video frame
        client_socket.sendall(message_type + message_size + data)
    vid.release()

def receive_data(client_socket):
    data = b""
    header_size = struct.calcsize("B") + struct.calcsize("L")  # 1-byte type + 4-byte size

    while True:
        # Receive header
        while len(data) < header_size:
            packet = client_socket.recv(4096)
            if not packet:
                break
            data += packet
        if not data:
            break

        # Unpack header
        message_type = struct.unpack("B", data[:1])[0]
        packed_msg_size = data[1:header_size]
        data = data[header_size:]
        msg_size = struct.unpack("L", packed_msg_size)[0]

        # Receive payload
        while len(data) < msg_size:
            data += client_socket.recv(4096)
        payload_data = data[:msg_size]
        data = data[msg_size:]

        if message_type == 2:
            # Process float array
            float_array = pickle.loads(payload_data)
            print("Received float array from server:", float_array)
            # Add your handling code here
        else:
            print(f"Unknown message type: {message_type}")

def audio_stream(client_socket):
    audio = pyaudio.PyAudio()
    stream = audio.open(format=pyaudio.paInt16, channels=1, rate=44100, input=True, frames_per_buffer=1024)

    try:
        while True:
            data = stream.read(1024)
            client_socket.sendall(data)
    except:
        pass
    finally:
        stream.stop_stream()
        stream.close()
        audio.terminate()

def main():
    video_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    audio_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    host_ip = 'localhost'  # Replace with your server's IP address
    video_port = 9999
    audio_port = 9998

    video_socket.connect((host_ip, video_port))
    audio_socket.connect((host_ip, audio_port))

    video_thread = threading.Thread(target=video_stream, args=(video_socket,))
    receive_thread = threading.Thread(target=receive_data, args=(video_socket,))
    audio_thread = threading.Thread(target=audio_stream, args=(audio_socket,))

    video_thread.start()
    receive_thread.start()
    audio_thread.start()

    video_thread.join()
    receive_thread.join()
    audio_thread.join()

if __name__ == '__main__':
    main()
