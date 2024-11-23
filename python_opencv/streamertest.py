import cv2
cap = cv2.VideoCapture(0)
if not cap.isOpened():
    print("Failed to open camera.")
else:
    print("Camera opened successfully.")
    ret, frame = cap.read()
    if ret:
        cv2.imshow("Frame", frame)
        cv2.waitKey(0)
    cap.release()
    cv2.destroyAllWindows()
