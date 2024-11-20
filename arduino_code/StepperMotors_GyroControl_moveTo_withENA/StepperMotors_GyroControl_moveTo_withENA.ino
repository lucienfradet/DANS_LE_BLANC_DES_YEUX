#include <Wire.h>
#include <math.h>
#include <AccelStepper.h>

// MPU-6050 Definitions
#define MPU6050_I2C_ADDRESS 0x68
#define FREQ  200.0 

// Stepper Motor Pins
#define NEMA17_DIR_PIN 4
#define NEMA17_STEP_PIN 5
#define NEMA17_ENA_PIN 8  // Enable pin for NEMA 17

#define NEMA23_DIR_PIN 6
#define NEMA23_STEP_PIN 7
#define NEMA23_ENA_PIN 9  // Enable pin for NEMA 23

#define SIGNAL_PIN 2  // Pin to deactivate motors on HIGH signal

AccelStepper stepperTilt(AccelStepper::DRIVER, NEMA17_STEP_PIN, NEMA17_DIR_PIN); 
AccelStepper stepperPan(AccelStepper::DRIVER, NEMA23_STEP_PIN, NEMA23_DIR_PIN);  

// Global 
double gSensitivity = 65.5; 
double gx = 0, gy = 0, gz = 0;
double gyrX = 0, gyrY = 0, gyrZ = 0;
int16_t accX = 0, accY = 0, accZ = 0;
double gyrXoffs = 0, gyrYoffs = 0, gyrZoffs = 0;
double roll = 0, pitch = 0, yaw = 0; // Angles in degrees

// Previous Motor Positions
long previousPositionTilt = 0;
long previousPositionPan = 0;

// Motor Steps per Degree
const int motor_steps_per_rev = 200; // 1.8 degrees per step
const int microstepping = 8;         // Adjust based on your TB6600 settings
const int steps_per_rev = motor_steps_per_rev * microstepping;
const double steps_per_degree = steps_per_rev / 360.0; // Steps per degree

// Threshold for movement detection (in degrees)
const double movementThreshold = 3.0; // Ignore movements smaller than this

void setup() {
  Serial.begin(9600);

  // Initialize I2C Communication
  Wire.begin();

  // MPU-6050 Initialization
  i2c_write_reg(MPU6050_I2C_ADDRESS, 0x6B, 0x00); // Wake up MPU-6050
  i2c_write_reg(MPU6050_I2C_ADDRESS, 0x1A, 0x01); // Configure DLPF
  i2c_write_reg(MPU6050_I2C_ADDRESS, 0x1B, 0x08); // Set gyro range to 500 deg/s
  i2c_write_reg(MPU6050_I2C_ADDRESS, 0x19, (1000 / FREQ) - 1); // Set sample rate

  // Calibrate Gyroscope
  calibrate();

  // Initialize Steppers
  stepperTilt.setMaxSpeed(2000);
  stepperTilt.setAcceleration(1000);

  stepperPan.setMaxSpeed(2000);
  stepperPan.setAcceleration(1000);

  // Set Enable Pins as OUTPUT
  pinMode(NEMA17_ENA_PIN, OUTPUT);
  pinMode(NEMA23_ENA_PIN, OUTPUT);

  // Set Signal Pin as INPUT
  pinMode(SIGNAL_PIN, INPUT_PULLUP);

  // Initially enable motors
  digitalWrite(NEMA17_ENA_PIN, LOW); // Active LOW to enable NEMA 17
  digitalWrite(NEMA23_ENA_PIN, LOW); // Active LOW to enable NEMA 23
}

void loop() {
  // Check the signal pin
  if (digitalRead(SIGNAL_PIN) == HIGH) {
    // Deactivate motors
    digitalWrite(NEMA17_ENA_PIN, HIGH); // Disable motor, spins freely
    digitalWrite(NEMA23_ENA_PIN, HIGH); // Disable motor, spins freely
    return; // Skip the rest of the loop to avoid running the motors
  }

  unsigned long start_time = millis();

  read_sensor_data();

  // Calculate Accelerometer Angles
  double accRoll = atan2(accY, accZ) * 180 / M_PI;
  double accPitch = atan2(-accX, sqrt(accY * accY + accZ * accZ)) * 180 / M_PI;

  // Gyro Integration for Roll and Pitch
  roll += gyrX / FREQ;
  pitch += gyrY / FREQ;

  // Complementary Filter for Roll and Pitch
  const double alpha = 0.98;
  roll = alpha * roll + (1 - alpha) * accRoll;
  pitch = alpha * pitch + (1 - alpha) * accPitch;

  // Gyro Integration for Yaw (Pan)
  yaw += gyrZ / FREQ;

  // Handle angle wrapping for yaw
  if (yaw >= 360.0) {
    yaw -= 360.0;
  } else if (yaw < 0.0) {
    yaw += 360.0;
  }

  // Check for significant movement
  if (abs(gyrX) < movementThreshold && abs(gyrY) < movementThreshold && abs(gyrZ) < movementThreshold) {
    // No significant movement detected, disable motors
    digitalWrite(NEMA17_ENA_PIN, HIGH); // Disable motor
    digitalWrite(NEMA23_ENA_PIN, HIGH); // Disable motor
  } else {
    // Significant movement detected, enable motors
    digitalWrite(NEMA17_ENA_PIN, LOW); // Enable motor
    digitalWrite(NEMA23_ENA_PIN, LOW); // Enable motor
  }

  // Map Angles to Motor Positions
  // Tilt (Roll)
  long targetPositionTilt = roll * steps_per_degree;

  // Adjust for wrapping
  long positionDifferenceTilt = targetPositionTilt - previousPositionTilt;
  if (positionDifferenceTilt > (steps_per_rev / 2)) {
    positionDifferenceTilt -= steps_per_rev;
  } else if (positionDifferenceTilt < -(steps_per_rev / 2)) {
    positionDifferenceTilt += steps_per_rev;
  }
  long newPositionTilt = previousPositionTilt + positionDifferenceTilt;
  stepperTilt.moveTo(newPositionTilt);
  previousPositionTilt = newPositionTilt;

  // Pan (Yaw)
  long targetPositionPan = yaw * steps_per_degree;

  // Adjust for wrapping
  long positionDifferencePan = targetPositionPan - previousPositionPan;
  if (positionDifferencePan > (steps_per_rev / 2)) {
    positionDifferencePan -= steps_per_rev;
  } else if (positionDifferencePan < -(steps_per_rev / 2)) {
    positionDifferencePan += steps_per_rev;
  }
  long newPositionPan = previousPositionPan + positionDifferencePan;
  stepperPan.moveTo(newPositionPan);
  previousPositionPan = newPositionPan;

  // Run Steppers
  stepperTilt.run();
  stepperPan.run();

  // Maintain Sample Rate
  unsigned long end_time = millis();
  int loop_time = end_time - start_time;
  if (loop_time < (1000 / FREQ)) {
    delay((1000 / FREQ) - loop_time);
  }
}

// Function to Calibrate Gyroscope
void calibrate() {
  int num_samples = 1000;
  long xSum = 0, ySum = 0, zSum = 0;

  for (int i = 0; i < num_samples; i++) {
    uint8_t i2cData[6];
    i2c_read(MPU6050_I2C_ADDRESS, 0x43, i2cData, 6);
    xSum += (int16_t)(i2cData[0] << 8 | i2cData[1]);
    ySum += (int16_t)(i2cData[2] << 8 | i2cData[3]);
    zSum += (int16_t)(i2cData[4] << 8 | i2cData[5]);
    delay(2);
  }

  gyrXoffs = xSum / num_samples;
  gyrYoffs = ySum / num_samples;
  gyrZoffs = zSum / num_samples;
}

// Function to Read Sensor Data
void read_sensor_data() {
  uint8_t i2cData[14];
  i2c_read(MPU6050_I2C_ADDRESS, 0x3B, i2cData, 14);

  accX = (int16_t)(i2cData[0] << 8 | i2cData[1]);
  accY = (int16_t)(i2cData[2] << 8 | i2cData[3]);
  accZ = (int16_t)(i2cData[4] << 8 | i2cData[5]);

  gyrX = ((int16_t)(i2cData[8] << 8 | i2cData[9]) - gyrXoffs) / gSensitivity;
  gyrY = ((int16_t)(i2cData[10] << 8 | i2cData[11]) - gyrYoffs) / gSensitivity;
  gyrZ = ((int16_t)(i2cData[12] << 8 | i2cData[13]) - gyrZoffs) / gSensitivity;
}

// I2C Read Function
int i2c_read(int addr, int reg, uint8_t *data, int length) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) {
    return -1;
  }
  Wire.requestFrom(addr, length, true);
  for (int i = 0; i < length && Wire.available(); i++) {
    data[i] = Wire.read();
  }
  return 0;
}

// I2C Write Function
int i2c_write_reg(int addr, int reg, uint8_t data) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.write(data);
  return Wire.endTransmission();
}
