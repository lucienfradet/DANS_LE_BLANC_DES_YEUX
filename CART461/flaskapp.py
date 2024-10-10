#OCT 9th, 2024
from flask import Flask, render_template, Response
from flask_cors import CORS
import cv2
import mediapipe as mp
import numpy as np
import time

#Help from this tutorial: https://www.youtube.com/watch?v=NAYb8SKUNyI

app = Flask(__name__)
CORS(app)

# Initialize MediaPipe Selfie Segmentation
mp_selfie_segmentation = mp.solutions.selfie_segmentation
selfie_segmentation = mp_selfie_segmentation.SelfieSegmentation(model_selection=0)

# Load the background image (replace with your path or use a solid color)
bg_image = cv2.imread('backgrounds/1.png')  # Ensure this path is correct or set to a solid color.

def generate_frames():
    cap = cv2.VideoCapture(0)  # Access the webcam
    prevTime = 0

    while cap.isOpened():
        success, image = cap.read()
        if not success:
            break

        # Flip the image for selfie view and convert from BGR to RGB
        image = cv2.cvtColor(cv2.flip(image, 1), cv2.COLOR_BGR2RGB)
        image.flags.writeable = False
        results = selfie_segmentation.process(image)
        image.flags.writeable = True
        image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

        # Create a mask based on segmentation results
        condition = np.stack((results.segmentation_mask,) * 3, axis=-1) > 0.1

        # If background image doesn't match the size, resize it
        if bg_image is None or bg_image.shape[:2] != image.shape[:2]:
            bg_image_fill = np.zeros(image.shape, dtype=np.uint8)
            bg_image_fill[:] = (0, 255, 196)  # Default green screen color
        else:
            bg_image_fill = cv2.resize(bg_image, (image.shape[1], image.shape[0]))

        # Apply the mask to combine the original image and the background
        output_image = np.where(condition, image, bg_image_fill)

        # Calculate and display FPS
        currTime = time.time()
        fps = 1 / (currTime - prevTime)
        prevTime = currTime
        cv2.putText(output_image, f'FPS: {int(fps)}', (20, 70), cv2.FONT_HERSHEY_PLAIN, 3, (0, 196, 255), 2)

        # Encode the frame in JPEG format
        ret, buffer = cv2.imencode('.jpg', output_image)
        frame = buffer.tobytes()

        # Yield the frame as part of a multipart response
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

    cap.release()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
