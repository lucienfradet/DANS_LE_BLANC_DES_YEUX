#line 1 "/home/lucienfradet/Arduino/CART461_PEOPLE_WATCHING/arduino_code_tests/send_gyro/send_gyro.ino"
#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <Arduino.h>
#include <Adafruit_HMC5883_U.h>

Adafruit_MPU6050 mpu;
Adafruit_HMC5883_Unified mag = Adafruit_HMC5883_Unified(12345);

double xOffset = 0, yOffset = 0, zOffset = 0;
double magZOffset = 0;

#line 13 "/home/lucienfradet/Arduino/CART461_PEOPLE_WATCHING/arduino_code_tests/send_gyro/send_gyro.ino"
void setup();
#line 61 "/home/lucienfradet/Arduino/CART461_PEOPLE_WATCHING/arduino_code_tests/send_gyro/send_gyro.ino"
void loop();
#line 13 "/home/lucienfradet/Arduino/CART461_PEOPLE_WATCHING/arduino_code_tests/send_gyro/send_gyro.ino"
void setup() {
  Serial.begin(9600);
  
  if (!mpu.begin()) {
    Serial.println("Failed to find MPU6050 chip");
    while (1) {
      delay(10);
    }
  }
  
  mpu.setAccelerometerRange(MPU6050_RANGE_2_G);
  mpu.setGyroRange(MPU6050_RANGE_250_DEG);
  mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);

  // Initialize HMC5883L
  if (!mag.begin()) {
    Serial.println("Failed to find HMC5883L chip");
    while (1) {
      delay(10);
    }
  }

  // Calibration phase
  delay(1000); // Allow MPU to stabilize

  const int calibrationSamples = 100;
  sensors_event_t accel, magEvent;
  long sumX = 0, sumY = 0, sumZ = 0, sumMagZ = 0;

  for (int i = 0; i < calibrationSamples; i++) {
    mpu.getAccelerometerSensor()->getEvent(&accel);
    mag.getEvent(&magEvent);

    sumX += accel.acceleration.x;
    sumY += accel.acceleration.y;
    sumZ += accel.acceleration.z;
    sumMagZ += magEvent.magnetic.z;

    delay(10); // Allow consistent readings
  }

  // Store the average values as offsets
  xOffset = sumX / (float)calibrationSamples;
  yOffset = sumY / (float)calibrationSamples;
  zOffset = sumZ / (float)calibrationSamples;
  magZOffset = sumMagZ / (float)calibrationSamples;
}

void loop() {
  sensors_event_t accel, magEvent;
  mpu.getAccelerometerSensor()->getEvent(&accel);
  mag.getEvent(&magEvent);

  // Subtract offsets to calculate adjusted angles
  double adjustedX = accel.acceleration.x - xOffset;
  double adjustedY = accel.acceleration.y - yOffset;
  double adjustedZ = accel.acceleration.z - zOffset;

  double xAngle = atan2(adjustedY, adjustedZ) * RAD_TO_DEG;
  double yAngle = atan2(adjustedX, adjustedZ) * RAD_TO_DEG;

  // Calculate Z-axis angle using only the HMC5883L X and Y data
  double zAngle = atan2(magEvent.magnetic.y, magEvent.magnetic.x) * RAD_TO_DEG;

  // Print angles
  Serial.print(xAngle);
  Serial.print(",");
  Serial.print(yAngle);
  Serial.print(",");
  Serial.println(zAngle);

  delay(100); // Adjust as needed
}

