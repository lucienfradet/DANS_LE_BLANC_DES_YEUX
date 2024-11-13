#include <Wire.h>

const int MPU_addr = 0x68;
int16_t AcX, AcY, AcZ;
int minVal = 265;
int maxVal = 402;

double x, y, z;
double xOffset = 0, yOffset = 0, zOffset = 0;

void setup() {
  Wire.begin();
  Wire.beginTransmission(MPU_addr);
  Wire.write(0x6B);  
  Wire.write(0);      
  Wire.endTransmission(true);
  Serial.begin(9600);

  // Calibration phase
//  Serial.println("Calibrating...");
  delay(1000);  // Give some time for MPU6050 to stabilize

  const int calibrationSamples = 100;
  long sumX = 0, sumY = 0, sumZ = 0;
  
  // Take multiple readings to calculate the average offsets
  for (int i = 0; i < calibrationSamples; i++) {
    Wire.beginTransmission(MPU_addr);
    Wire.write(0x3B);
    Wire.endTransmission(false);
    Wire.requestFrom(MPU_addr, 6, true);
    AcX = Wire.read() << 8 | Wire.read();
    AcY = Wire.read() << 8 | Wire.read();
    AcZ = Wire.read() << 8 | Wire.read();

    // Calculate initial angles
    int xAng = map(AcX, minVal, maxVal, -90, 90);
    int yAng = map(AcY, minVal, maxVal, -90, 90);
    int zAng = map(AcZ, minVal, maxVal, -90, 90);

    // Store the initial orientation angles as offsets
    xOffset += RAD_TO_DEG * (atan2(-yAng, -zAng) + PI);
    yOffset += RAD_TO_DEG * (atan2(-xAng, -zAng) + PI);
    zOffset += RAD_TO_DEG * (atan2(-yAng, -xAng) + PI);

    delay(10);  // Small delay for consistent readings
  }

  // Average out the offsets over the calibration samples
  xOffset /= calibrationSamples;
  yOffset /= calibrationSamples;
  zOffset /= calibrationSamples;

//  Serial.println("Calibration complete");
}

void loop() {
  Wire.beginTransmission(MPU_addr);
  Wire.write(0x3B);
  Wire.endTransmission(false);
  Wire.requestFrom(MPU_addr, 14, true);

  AcX = Wire.read() << 8 | Wire.read();
  AcY = Wire.read() << 8 | Wire.read();
  AcZ = Wire.read() << 8 | Wire.read();

  // Calculate angles based on accelerometer data
  int xAng = map(AcX, minVal, maxVal, -90, 90);
  int yAng = map(AcY, minVal, maxVal, -90, 90);
  int zAng = map(AcZ, minVal, maxVal, -90, 90);

  // Calculate orientation angles, subtracting the offsets to zero out the initial position
  x = RAD_TO_DEG * (atan2(-yAng, -zAng) + PI) - xOffset;
  y = RAD_TO_DEG * (atan2(-xAng, -zAng) + PI) - yOffset;
  z = RAD_TO_DEG * (atan2(-yAng, -xAng) + PI) - zOffset;

  // Print the calculated angles (with the initial position now at 0, 0, 0)
  Serial.print(x);
  Serial.print(",");
  Serial.print(y);
  Serial.print(",");
  Serial.println(z);

  delay(50);  // Adjust delay as needed
}
