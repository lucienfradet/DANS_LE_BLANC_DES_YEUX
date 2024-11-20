import cv2
import numpy as np

def chroma_key(frame, lower_bound, upper_bound):
    # Convert the frame to HSV color space
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    
    # Create a mask for colors within the specified range
    mask = cv2.inRange(hsv, lower_bound, upper_bound)
    
    # Apply the mask to turn black areas white
    result = frame.copy()
    result[mask > 0] = [255, 255, 255]  # Set masked areas to white
    
    return result

def add_dithering_effect(frame, pixel_size=10):
    # Resize to a smaller size to create pixelation
    height, width = frame.shape[:2]
    small_frame = cv2.resize(frame, (width // pixel_size, height // pixel_size), interpolation=cv2.INTER_LINEAR)
    
    # Scale it back to original size
    pixelated_frame = cv2.resize(small_frame, (width, height), interpolation=cv2.INTER_NEAREST)
    
    return pixelated_frame

# Initialize webcam
cap = cv2.VideoCapture(0)

# Hardcoded HSV thresholds
lower_black = np.array([0, 0, 0])       # Lower bound for dark areas
upper_black = np.array([180, 255, 120]) # Upper bound for dark areas

# Main loop
while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        break

    # Apply chroma keying with hardcoded values
    chroma_frame = chroma_key(frame, lower_black, upper_black)

    # Add dithering/pixelation effect
    pixelated_frame = add_dithering_effect(chroma_frame, pixel_size=10)  # Adjust pixel_size for different levels of pixelation

    # Display the resulting frame
    cv2.imshow('Chroma Keyed Video', pixelated_frame)

    # Exit on 'q' key press
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# Release the capture and close windows
cap.release()
cv2.destroyAllWindows()
