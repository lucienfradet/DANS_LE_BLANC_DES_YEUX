// MPU-6050 Accelerometer + Gyro
// code inspired by mattzzw's project: https://github.com/mattzzw/Arduino-mpu6050

// MPU on A4, A5

#include "Arduino.h"
#include <Wire.h>
#include <math.h>
#include <AccelStepper.h>

#define MPU6050_I2C_ADDRESS 0x68

#define FREQ  30.0 // sample freq in Hz

// Stepper Motor Pins
#define NEMA17_DIR_PIN 4
#define NEMA17_PUL_PIN 5

#define NEMA23_DIR_PIN 6
#define NEMA23_PUL_PIN 7

#define PRESSURE_PLATE_SIGNAL 8    // Pin receiving motor enable from pressure plate

AccelStepper stepperTilt(AccelStepper::DRIVER, NEMA17_PUL_PIN, NEMA17_DIR_PIN); 
AccelStepper stepperPan(AccelStepper::DRIVER, NEMA23_PUL_PIN, NEMA23_DIR_PIN);  

//NEMA variables:
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
const double tiltMax = 50; // min and max for tilt movements

//MPU variables:
// global angle, gyro derived
double gSensitivity = 65.5; // for 500 deg/s, check data sheet
double gx = 0, gy = 0, gz = 0;
double gyrX = 0, gyrY = 0, gyrZ = 0;
int16_t accX = 0, accY = 0, accZ = 0;

double gyrXoffs = -281.00, gyrYoffs = 18.00, gyrZoffs = -83.00;

void setup()
{      
  int error;
  uint8_t c;
  uint8_t sample_div;

  Serial.begin(9600);

  // Initialize the 'Wire' class for the I2C-bus.
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
  calibrate();
//  Serial.write("done.");

  // Initialize Steppers
  stepperTilt.setMaxSpeed(2000);
  stepperTilt.setAcceleration(1000);

  stepperPan.setMaxSpeed(2000);
  stepperPan.setAcceleration(1000);

  // set pressure plate pin as input
  pinMode(PRESSURE_PLATE_SIGNAL, INPUT);
}

bool isNumber(String str) {
    for (unsigned int i = 0; i < str.length(); i++) {
        if (!isDigit(str[i])) {
            return false;  // Return false if any character is not a digit
        }
    }
    return true;  // Return true if all characters are digits
}

void loop()
{
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
  gx = gx + gyrX / FREQ;
  gy = gy - gyrY / FREQ;
  gz = gz + gyrZ / FREQ;

  // complementary filter
  // tau = DT*(A)/(1-A)
  // = 0.48sec
  gx = gx * 0.96 + ax * 0.04;
  gy = gy * 0.96 + ay * 0.04;

  // check if there is some kind of request 
  // from the other side...
  if(Serial.available())
  {
    String input_string;
    // dummy read
    input_string = Serial.readStringUntil('\n');
    input_string.trim();
    // we have to send data, as requested
    if (input_string == "."){

      // Handle angle wrapping for gz
      if (gz >= 360.0) {
        gz -= 360.0;
      } else if (gz < 0.0) {
        gz += 360.0;
      }
      
      Serial.print("y: ");
      Serial.print(round(gy));
      // Serial.print(gy, 2);
      Serial.print(", z: ");
      Serial.print(round(gz));
      // Serial.print(gz, 2);
      Serial.print(", pressure: ");
      Serial.println(digitalRead(PRESSURE_PLATE_SIGNAL));
    }
    // reset z axis command
    else if (input_string == "z"){
      gz = 0;
    }
    // move to received y and z positions
    else if (input_string.length() > 0) {
      // Check if input has a single comma
      int commaIndex = input_string.indexOf(',');
      if (commaIndex == -1 || commaIndex == 0 || commaIndex == input_string.length() - 1) {
        Serial.println("Parsing failed: Invalid format");
      } 
      else {
        // Split into two parts
        String yString = input_string.substring(0, commaIndex);
        String zString = input_string.substring(commaIndex + 1);
        yString.trim();
        zString.trim();

        // Check if both parts are digits
        if (isNumber(yString) && isNumber(zString)) {
          int targetY = yString.toInt();
          int targetZ = zString.toInt();

          // Check for significant movement
          if (abs(gyrX) < movementThreshold && abs(gyrY) < movementThreshold && abs(gyrZ) < movementThreshold) {
            // No significant movement detected, continue
          } else {
            // Significant movement detected, move motors

            // Map Angles to Motor Positions
            // Tilt (Roll)
            long targetPositionTilt = targetY * steps_per_degree;

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
            long targetPositionPan = targetZ * steps_per_degree;

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
          }
        }
      }
    } 
  }

  end_time = millis();

  // remaining time to complete sample time
  delay(((1/FREQ) * 1000) - (end_time - start_time));
  //Serial.println(end_time - start_time);
}


void calibrate(){

  int x;
  long xSum = 0, ySum = 0, zSum = 0;
  uint8_t i2cData[6]; 
  int num = 500;
  uint8_t error;

  for (x = 0; x < num; x++){

    error = i2c_read(MPU6050_I2C_ADDRESS, 0x43, i2cData, 6);
    if(error!=0)
    return;

    xSum += ((i2cData[0] << 8) | i2cData[1]);
    ySum += ((i2cData[2] << 8) | i2cData[3]);
    zSum += ((i2cData[4] << 8) | i2cData[5]);
  }
  gyrXoffs = xSum / num;
  gyrYoffs = ySum / num;
  gyrZoffs = zSum / num;
} 

void read_sensor_data(){
 uint8_t i2cData[14];
 uint8_t error;
 // read imu data
 error = i2c_read(MPU6050_I2C_ADDRESS, 0x3b, i2cData, 14);
 if(error!=0)
 return;

 // assemble 16 bit sensor data
 accX = ((i2cData[0] << 8) | i2cData[1]);
 accY = ((i2cData[2] << 8) | i2cData[3]);
 accZ = ((i2cData[4] << 8) | i2cData[5]);

 gyrX = (((i2cData[8] << 8) | i2cData[9]) - gyrXoffs) / gSensitivity;
 gyrY = (((i2cData[10] << 8) | i2cData[11]) - gyrYoffs) / gSensitivity;
 gyrZ = (((i2cData[12] << 8) | i2cData[13]) - gyrZoffs) / gSensitivity;
 
}

// ---- I2C routines

int i2c_read(int addr, int start, uint8_t *buffer, int size)
{
  int i, n, error;

  Wire.beginTransmission(addr);
  n = Wire.write(start);
  if (n != 1)
  return (-10);

  n = Wire.endTransmission(false);    // hold the I2C-bus
  if (n != 0)
  return (n);

  // Third parameter is true: relase I2C-bus after data is read.
  Wire.requestFrom(addr, size, true);
  i = 0;
  while(Wire.available() && i<size)
  {
    buffer[i++]=Wire.read();
  }
  if ( i != size)
  return (-11);

  return (0);  // return : no error
}


int i2c_write(int addr, int start, const uint8_t *pData, int size)
{
  int n, error;

  Wire.beginTransmission(addr);
  n = Wire.write(start);        // write the start address
  if (n != 1)
  return (-20);

  n = Wire.write(pData, size);  // write data bytes
  if (n != size)
  return (-21);

  error = Wire.endTransmission(true); // release the I2C-bus
  if (error != 0)
  return (error);

  return (0);         // return : no error
}


int i2c_write_reg(int addr, int reg, uint8_t data)
{
  int error;
  
  error = i2c_write(addr, reg, &data, 1);
  return (error);
}
