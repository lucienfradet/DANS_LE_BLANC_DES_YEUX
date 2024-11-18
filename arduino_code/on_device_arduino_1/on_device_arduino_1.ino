#define PRESSURE_SENSOR_PIN A0
#define MOTOR_ENABLE_PIN 8    // Pin controlling motor enable (adjust as needed)

#include "Arduino.h"
#include <AccelStepper.h>
#include <MultiStepper.h>
#include <Wire.h>
#include <math.h>

const int threshold = 250;

void setup() {
  Serial.begin(9600);

  pinMode(MOTOR_ENABLE_PIN, OUTPUT);
  digitalWrite(MOTOR_ENABLE_PIN, LOW); // Initially enable motors

<<<<<<< HEAD
  // Other setup code for your motors and sensors
=======
  Wire.begin();

  // PWR_MGMT_1:
  // wake up 
  i2c_write_reg (MPU6050_I2C_ADDRESS, 0x6b, 0x00);

  // CONFIG:
  // Low pass filter samples, 1khz sample rate
  i2c_write_reg (MPU6050_I2C_ADDRESS, 0x1a, 0x01);

  // GYRO_CONFIG:
  // 500 deg/s, FS_SEL=1
  // This means 65.5 LSBs/deg/s
  i2c_write_reg(MPU6050_I2C_ADDRESS, 0x1b, 0x08);

  // CONFIG:
  // set sample rate
  // sample rate FREQ = Gyro sample rate / (sample_div + 1)
  // 1kHz / (div + 1) = FREQ  
  // reg_value = 1khz/FREQ - 1
  sample_div = 1000 / FREQ - 1;
  i2c_write_reg (MPU6050_I2C_ADDRESS, 0x19, sample_div);


//  Serial.write("Calibrating...");
  digitalWrite(13, HIGH);
  calibrate();
  digitalWrite(13, LOW);

  // Configure stepper parameters
  stepperTilt.setMaxSpeed(500.0);
  stepperTilt.setAcceleration(500.0);

  stepperYaw.setMaxSpeed(1000.0);
  stepperYaw.setAcceleration(600.0);

  // Add steppers to MultiStepper manager
  steppers.addStepper(stepperYaw);
  steppers.addStepper(stepperTilt);
>>>>>>> 27355b31b8775f45003136a5c5a0ccab2660d983
}

void loop() {
  int sensorValue = analogRead(PRESSURE_SENSOR_PIN);

  Serial.print("Sensor Value: ");
  Serial.println(sensorValue);

<<<<<<< HEAD
  if (sensorValue > threshold) {
    // Deactivate motors
    digitalWrite(MOTOR_ENABLE_PIN, HIGH); // Assuming HIGH disables the motors
  } else {
    // Activate motors
    digitalWrite(MOTOR_ENABLE_PIN, LOW);  // Assuming LOW enables the motors
=======
    // Split the input by commas and parse y, z values
    int commaIndex1 = input.indexOf(',');
    int commaIndex2 = input.lastIndexOf(',');
    
    if (commaIndex1 != -1 && commaIndex2 != -1 && commaIndex1 != commaIndex2) {
      String yString = input.substring(commaIndex1 + 1, commaIndex2);
      String zString = input.substring(commaIndex2 + 1);

      int yValue = yString.toInt();
      int zValue = zString.toInt();

      // Move steppers to new positions based on y and z values
      long positions[2];
      //deg * 200 steps full rotation / 360 degs
      positions[0] = zValue * 200 * 4 / 360;  // z for yaw
      yValue = constrain(yValue, -60, 60);     // Clamp yValue to -45 to 45
      positions[1] = yValue;  // y for tilt


      stepperYaw.moveTo(positions[0]);
      stepperTilt.moveTo(positions[1]);
      stepperYaw.run();
      stepperTilt.run();

      // steppers.moveTo(positions);
      // steppers.runSpeedToPosition(); // Blocks until all steppers are in position

      unsigned long currentTime = millis();
      // Check MPU every 2 seconds
      if (currentTime - lastUpdateTime >= interval) {
        lastUpdateTime = currentTime;
        int error;
        double dT;
        double ax, ay, az;
        unsigned long start_time, end_time;

        start_time = millis();

        read_sensor_data();

        // angles based on accelerometer
        ay = atan2(accX, sqrt( pow(accY, 2) + pow(accZ, 2))) * 180 / M_PI;
        ax = atan2(accY, sqrt( pow(accX, 2) + pow(accZ, 2))) * 180 / M_PI;

        // angles based on gyro (deg/s)
        // gx = gx + gyrX / FREQ;
        // gy = gy - gyrY / FREQ;
        gz = gz + gyrZ / FREQ;

        // complementary filter
        // tau = DT*(A)/(1-A)
        // = 0.48sec
        // gx = gx * 0.96 + ax * 0.04;
        // gy = gy * 0.96 + ay * 0.04;
        Serial.println(gz);
        
        float zDelta = 0;
        if (zValue > 0 && gz > 0 || zValue < 0 && gz < 0) {
          zDelta = zValue - gz;
        }
        else {
          zDelta = abs(zValue) + abs(gz);
          if (zValue < 0) {
            zDelta *= -1;
          }
        }

        if (zDelta > 10) {
          positions[0] = zDelta * 200 / 360;  // z for yaw
          yValue = constrain(yValue, -45, 45);     // Clamp yValue to -45 to 45
          positions[1] = yValue;  // y for tilt


          // steppers.moveTo(positions);
          // steppers.runSpeedToPosition(); // Blocks until all steppers are in position
        }

        end_time = millis();

        // remaining time to complete sample time
        delay(((1/FREQ) * 1000) - (end_time - start_time));
        //Serial.println(end_time - start_time);
      }
    }
>>>>>>> 27355b31b8775f45003136a5c5a0ccab2660d983
  }
  Serial.println(digitalRead(8));
  // Rest of your code (e.g., controlling steppers, reading other sensors)

  // Optional delay
  delay(100);
}
