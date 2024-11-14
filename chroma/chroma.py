import cv2
import numpy as np

def chroma_key(frame, lower_bound, upper_bound):
    # Convert the frame to HSV color space
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    
    # Create a mask for dark colors within the specified range
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

# Callback function for trackbars (does nothing but is required by OpenCV)
def nothing(x):
    pass

# Initialize webcam
cap = cv2.VideoCapture(0)

# Create a window with trackbars to adjust HSV range
cv2.namedWindow('Chroma Keyed Video')
cv2.createTrackbar('Lower Hue Threshold', 'Chroma Keyed Video', 0, 180, nothing)
cv2.createTrackbar('Upper Hue Threshold', 'Chroma Keyed Video', 180, 180, nothing)
cv2.createTrackbar('Lower Saturation Threshold', 'Chroma Keyed Video', 0, 255, nothing)
cv2.createTrackbar('Upper Saturation Threshold', 'Chroma Keyed Video', 255, 255, nothing)
cv2.createTrackbar('Lower Brightness Threshold', 'Chroma Keyed Video', 0, 255, nothing)
cv2.createTrackbar('Upper Brightness Threshold', 'Chroma Keyed Video', 115, 255, nothing)  # Start at 115 for your setup

while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        break

    # Get current positions of trackbars
    lower_h = cv2.getTrackbarPos('Lower Hue Threshold', 'Chroma Keyed Video')
    upper_h = cv2.getTrackbarPos('Upper Hue Threshold', 'Chroma Keyed Video')
    lower_s = cv2.getTrackbarPos('Lower Saturation Threshold', 'Chroma Keyed Video')
    upper_s = cv2.getTrackbarPos('Upper Saturation Threshold', 'Chroma Keyed Video')
    lower_v = cv2.getTrackbarPos('Lower Brightness Threshold', 'Chroma Keyed Video')
    upper_v = cv2.getTrackbarPos('Upper Brightness Threshold', 'Chroma Keyed Video')

    # Set HSV range based on slider positions
    lower_black = np.array([lower_h, lower_s, lower_v])
    upper_black = np.array([upper_h, upper_s, upper_v])

    # Apply chroma keying with adjusted values
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
