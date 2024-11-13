import cv2
import numpy as np

def chroma_key(frame, lower_bound, upper_bound):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, lower_bound, upper_bound)
    mask_inv = cv2.bitwise_not(mask)
    foreground = cv2.bitwise_and(frame, frame, mask=mask_inv)
    background = np.zeros_like(frame, dtype=np.uint8)
    combined = cv2.add(background, foreground)
    return combined

# Callback function for trackbars (required by OpenCV but does nothing here)
def nothing(x):
    pass

# Initialize webcam
cap = cv2.VideoCapture(0)

# Create a window with trackbars for adjusting HSV range
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

    # Update HSV range for dark colors
    lower_black = np.array([0, 0, lower_v])
    upper_black = np.array([180, 255, upper_v])

    # Apply chroma keying
    output_frame = chroma_key(frame, lower_black, upper_black)
    
    # Display the resulting frame
    cv2.imshow('Chroma Keyed Video', output_frame)

    # Exit on 'q' key press
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# Release the capture and close windows
cap.release()
cv2.destroyAllWindows()
