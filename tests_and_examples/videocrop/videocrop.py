import cv2
import numpy as np

# Load the Haar Cascade for eye detection
eye_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_eye.xml')

# Initialize the main camera (camera 0)
cap_main = cv2.VideoCapture(0)

if not cap_main.isOpened():
    print("Error: Could not access the main camera.")
    exit()

# Variables to stabilize detection
last_position = None  # Store the last known box position

# Define the consistent box size
BOX_WIDTH = 300
BOX_HEIGHT = 100

while cap_main.isOpened():
    # Read frames from the main camera
    ret_main, frame_main = cap_main.read()
    if not ret_main:
        print("Error: Could not read frame from main camera.")
        break

    # Flip the frame horizontally for a natural mirror-like view
    frame_main = cv2.flip(frame_main, 1)

    # Convert the frame to grayscale for eye detection
    gray_frame = cv2.cvtColor(frame_main, cv2.COLOR_BGR2GRAY)

    # Detect eyes in the frame
    eyes = eye_cascade.detectMultiScale(gray_frame, scaleFactor=1.1, minNeighbors=10, minSize=(50, 50))

    # Create a blank frame for the output
    blank_frame = np.zeros_like(frame_main)

    # If eyes are detected, update the box position
    if len(eyes) > 0:
        # Use the first detected eye and calculate the center of the box
        x, y, w, h = eyes[0]
        center_x, center_y = x + w // 2, y + h // 2

        # Calculate the top-left corner of the box
        top_left_x = max(0, center_x - BOX_WIDTH // 2)
        top_left_y = max(0, center_y - BOX_HEIGHT // 2)

        # Ensure the box does not exceed frame boundaries
        top_left_x = min(top_left_x, frame_main.shape[1] - BOX_WIDTH)
        top_left_y = min(top_left_y, frame_main.shape[0] - BOX_HEIGHT)

        # Update the last known position
        last_position = (top_left_x, top_left_y)
    elif last_position:
        # Use the last known position if no eyes are detected
        top_left_x, top_left_y = last_position
    else:
        # Default to the center of the frame if no detection yet
        top_left_x = (frame_main.shape[1] - BOX_WIDTH) // 2
        top_left_y = (frame_main.shape[0] - BOX_HEIGHT) // 2
        last_position = (top_left_x, top_left_y)

    # Crop the consistent box area from the main video
    cropped_frame = frame_main[top_left_y:top_left_y + BOX_HEIGHT, top_left_x:top_left_x + BOX_WIDTH]

    # Overlay the cropped frame (consistent box) onto the blank frame
    blank_frame[top_left_y:top_left_y + BOX_HEIGHT, top_left_x:top_left_x + BOX_WIDTH] = cropped_frame

    # Display the processed video
    cv2.imshow('Stable Eye Tracking with Consistent Box', blank_frame)

    # Exit on 'q' key press
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# Release the capture and close windows
cap_main.release()
cv2.destroyAllWindows()
