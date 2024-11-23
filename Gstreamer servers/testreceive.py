import socket
import numpy as np
import cv2
import struct
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

# Define the host and port to listen on
HOST = "0.0.0.0"  # Listen on all interfaces
PORT = 5000

BUFFER_SIZE = 4096

# Create a TCP socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # Allow address reuse
    server_socket.bind((HOST, PORT))
    server_socket.listen(1)  # Listen for a single connection
    print(f"Listening on {HOST}:{PORT}...")

    conn, addr = server_socket.accept()
    print(f"Connection from {addr}")

    try:
        while True:
            # Receive the size of the incoming frame (4 bytes)
            frame_size_data = conn.recv(4)
            if not frame_size_data:
                break
            frame_size = struct.unpack(">I", frame_size_data)[0]

            # Receive the actual frame data
            data = b""
            while len(data) < frame_size:
                chunk = conn.recv(min(BUFFER_SIZE, frame_size - len(data)))
                if not chunk:
                    break
                data += chunk

            # Decode the received frame
            np_data = np.frombuffer(data, np.uint8)
            frame = cv2.imdecode(np_data, cv2.IMREAD_COLOR)

            if frame is not None:
                # Display the frame in a window
                cv2.imshow("Received Video Feed", frame)

                # Exit if 'q' is pressed
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
            else:
                print("Failed to decode frame.")
    except KeyboardInterrupt:
        print("Streaming stopped by user.")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        cv2.destroyAllWindows()
