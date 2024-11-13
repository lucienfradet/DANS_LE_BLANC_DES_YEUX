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

# Callback function for trackbars (does nothing but is required by OpenCV)
def nothing(x):
    pass

# Initialize webcam
cap = cv2.VideoCapture(0)

# Create a window with trackbars to adjust HSV range
cv2.namedWindow('Chroma Keyed Video')
cv2.createTrackbar('Lower V', 'Chroma Keyed Video', 0, 255, nothing)
cv2.createTrackbar('Upper V', 'Chroma Keyed Video', 50, 255, nothing)

while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        break

    # Get current positions of trackbars
    lower_v = cv2.getTrackbarPos('Lower V', 'Chroma Keyed Video')
    upper_v = cv2.getTrackbarPos('Upper V', 'Chroma Keyed Video')

    # Set HSV range for dark colors
    lower_black = np.array([0, 0, lower_v])
    upper_black = np.array([180, 255, upper_v])

    # Apply chroma keying with adjusted values
    output_frame = chroma_key(frame, lower_black, upper_black)
    
    # Display the resulting frame
    cv2.imshow('Chroma Keyed Video', output_frame)

    # Exit on 'q' key press
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# Release the capture and close windows
cap.release()
cv2.destroyAllWindows()
