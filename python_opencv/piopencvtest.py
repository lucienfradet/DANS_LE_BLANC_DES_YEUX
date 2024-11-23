import cv2
import numpy as np

# Create a blank image
image = np.zeros((300, 300, 3), dtype=np.uint8)

# Draw something on the image
cv2.putText(image, "Test Window", (50, 150), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)

# Display the image
cv2.imshow("Test Window", image)
cv2.waitKey(0)
cv2.destroyAllWindows()
