# server.py
import socket
import threading
import pickle
import struct
import pygame
import sys
import cv2
import numpy as np
import pyaudio
import PIL as pil

palette = np.array([(255, 0, 0), (0, 255, 0), (0, 0, 255)])

def video_stream(client_socket):
    pygame.init()
    display_surface = None
    data = b""
    header_size = struct.calcsize("B") + struct.calcsize("L")  # 1-byte type + 4-byte size
    # pygame.display.set_palette(palette)
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

        if message_type == 1:
            # Process video frame
            buffer = pickle.loads(payload_data)
            frame = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame = cv2.transpose(frame)
            # frame = dithering(frame, 8) # doesnt work rn its PIL based we gotta do dithering in python
            frame = pygame.surfarray.make_surface(frame)

            if display_surface is None:
                #display_surface = pygame.display.set_mode((frame.get_width(), frame.get_height()))
                display_surface = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()

            # **Detect Key Press and Send Float Array**
            keys = pygame.key.get_pressed()
            if keys[pygame.K_SPACE]:  # Change to your desired key
                # Prepare float array
                float_array = [1.0, 2.0, 3.0, 4.0]  # Example float values
                data_to_send = pickle.dumps(float_array)
                message_size = struct.pack("L", len(data_to_send))
                message_type = struct.pack("B", 2)  # Message type 2 for float array
                client_socket.sendall(message_type + message_size + data_to_send)
            
           # display_surface.set_palette(palette) #dont work becuase colors arent indexed, likely due to the palette variable being declared incorrectly or something
            display_surface.blit(frame, (0, 0))
            pygame.display.update()
        else:
            print(f"Unknown message type: {message_type}")

def audio_stream(client_socket):
    audio = pyaudio.PyAudio()
    stream = audio.open(format=pyaudio.paInt16, channels=1, rate=44100, output=True, frames_per_buffer=1024)

    try:
        while True:
            data = client_socket.recv(1024)
            if not data:
                break
            stream.write(data)
    except:
        pass
    finally:
        stream.stop_stream()
        stream.close()
        audio.terminate()

def main():
    video_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    audio_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    host_ip = '0.0.0.0'
    video_port = 9999
    audio_port = 9998

    video_socket.bind((host_ip, video_port))
    audio_socket.bind((host_ip, audio_port))

    video_socket.listen(5)
    audio_socket.listen(5)
    print(f"Server listening for video at {host_ip}:{video_port}")
    print(f"Server listening for audio at {host_ip}:{audio_port}")

    video_client_socket, addr = video_socket.accept()
    print(f'Got video connection from {addr}')
    audio_client_socket, addr = audio_socket.accept()
    print(f'Got audio connection from {addr}')

    video_thread = threading.Thread(target=video_stream, args=(video_client_socket,))
    audio_thread = threading.Thread(target=audio_stream, args=(audio_client_socket,))

    video_thread.start()
    audio_thread.start()

    video_thread.join()
    audio_thread.join()

def get_new_val(old_val, nc): #dithering related
    return np.round(old_val * (nc - 1)) / (nc - 1)

def dithering(thisFrame, ditherStrength):
    width, height = thisFrame.size
    arr = np.array(thisFrame, dtype=float) / 255
    for ir in range(height):
        for ic in range(width):
            old_val = arr[ir, ic].copy()
            new_val = get_new_val(old_val, ditherStrength)
            arr[ir, ic] = new_val
            err = old_val - new_val
            if ic < width - 1:
                arr[ir, ic+1] += err * 7/16
            if ir < height - 1:
                if ic > 0:
                    arr[ir+1, ic-1] += err * 3/16
                arr[ir+1, ic] += err * 5/16
                if ic < width - 1:
                    arr[ir+1, ic+1] += err / 16
    carr = np.array(arr/np.max(arr, axis=(0,1)) * 255, dtype=np.uint8)
    return pil.fromarray(carr)

if __name__ == '__main__':
    main()
