#include <Arduino.h>
#line 1 "/home/lucienfradet/Arduino/CART461_PEOPLE_WATCHING/arduino_code_tests/homming-stepper/homming-stepper.ino"
#include <Wire.h>
#include <AccelStepper.h>

// MPU6050 settings
const int MPU_ADDR = 0x68;
const int PWR_MGMT_1 = 0x6B;

// Stepper pins
const int STEP_PIN = 2;
const int DIR_PIN = 3;
const int ENABLE_PIN = 4;

// Movement constants
const float STEPS_PER_DEGREE = 200.0 / 360.0 * 16;  // Assuming 16x microstepping
const float MIN_PITCH = -30.0;  // Maximum down angle
const float MAX_PITCH = 30.0;   // Maximum up angle

// Create stepper instance
AccelStepper stepper(AccelStepper::DRIVER, STEP_PIN, DIR_PIN);

// Tracking variables
float currentPitch = 0;
bool isHomed = false;
float homeOffset = 0;  // Will be calibrated on startup

#line 26 "/home/lucienfradet/Arduino/CART461_PEOPLE_WATCHING/arduino_code_tests/homming-stepper/homming-stepper.ino"
void setup();
#line 46 "/home/lucienfradet/Arduino/CART461_PEOPLE_WATCHING/arduino_code_tests/homming-stepper/homming-stepper.ino"
void loop();
#line 79 "/home/lucienfradet/Arduino/CART461_PEOPLE_WATCHING/arduino_code_tests/homming-stepper/homming-stepper.ino"
void autoHome();
#line 110 "/home/lucienfradet/Arduino/CART461_PEOPLE_WATCHING/arduino_code_tests/homming-stepper/homming-stepper.ino"
float getPitchFromMPU();
#line 127 "/home/lucienfradet/Arduino/CART461_PEOPLE_WATCHING/arduino_code_tests/homming-stepper/homming-stepper.ino"
long pitchToSteps(float pitch);
#line 131 "/home/lucienfradet/Arduino/CART461_PEOPLE_WATCHING/arduino_code_tests/homming-stepper/homming-stepper.ino"
void initMPU6050();
#line 26 "/home/lucienfradet/Arduino/CART461_PEOPLE_WATCHING/arduino_code_tests/homming-stepper/homming-stepper.ino"
void setup() {
  Serial.begin(9600);
  // Serial.println("Binocular Control Starting...");
  
  // Initialize MPU6050
  Wire.begin();
  initMPU6050();
  delay(1000);  // Let MPU6050 stabilize
  
  // Initialize stepper
  pinMode(ENABLE_PIN, OUTPUT);
  digitalWrite(ENABLE_PIN, LOW);  // Enable stepper
  
  stepper.setMaxSpeed(1000);
  stepper.setAcceleration(500);
  
  // Auto-home using MPU6050
  autoHome();
}

void loop() {
  // Get absolute pitch from MPU6050
  float measuredPitch = getPitchFromMPU();
  
  // Apply calibration offset
  // float calibratedPitch = measuredPitch - homeOffset;
  
  // Map pitch to our constrained range
  // float targetPitch = constrain(measuredPitch, MIN_PITCH, MAX_PITCH);
  
  // Convert to steps
  // long targetSteps = pitchToSteps(targetPitch);
  Serial.print("0,");
  Serial.print(measuredPitch);
  Serial.println(",0");
  delay(50);

  // Update stepper
  // stepper.run();
  
  // Debug output every 500ms
  // static unsigned long lastPrint = 0;
  // if (millis() - lastPrint > 500) {
  //   Serial.print("Raw Pitch: ");
  //   Serial.print(measuredPitch);
  //   Serial.print(" Calibrated: ");
  //   Serial.print(calibratedPitch);
  //   Serial.print(" Target: ");
  //   Serial.println(targetPitch);
  //   lastPrint = millis();
  // }
}

void autoHome() {
  // Serial.println("Auto-homing sequence started...");
  
  // Take multiple readings to get a stable value
  float sumPitch = 0;
  const int NUM_SAMPLES = 10;
  
  for(int i = 0; i < NUM_SAMPLES; i++) {
    sumPitch += getPitchFromMPU();
    delay(100);
  }
  
  // Average the readings
  float currentPitch = sumPitch / NUM_SAMPLES;
  
  // Set this as our reference point
  homeOffset = currentPitch;
  stepper.setCurrentPosition(0);
  
  // Serial.print("Home offset calibrated to: ");
  // Serial.println(homeOffset);
  
  // Move to center position (0 degrees)
  // stepper.moveTo(pitchToSteps(0));
  // while(stepper.isRunning()) {
  //   stepper.run();
  // }
  
  // Serial.println("Auto-homing complete!");
}

float getPitchFromMPU() {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);  // Starting register for accel readings
  Wire.endTransmission(false);
  Wire.requestFrom(MPU_ADDR, 6, true);
  
  int16_t ax = Wire.read() << 8 | Wire.read();
  int16_t ay = Wire.read() << 8 | Wire.read();
  int16_t az = Wire.read() << 8 | Wire.read();
  
  // Calculate pitch using accelerometer
  // atan2 gives us absolute angle relative to gravity
  float pitch = atan2(ax, sqrt(ay * ay + az * az)) * 180.0 / M_PI;
  
  return pitch;
}

long pitchToSteps(float pitch) {
  return pitch * STEPS_PER_DEGREE;
}

void initMPU6050() {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(PWR_MGMT_1);
  Wire.write(0);
  Wire.endTransmission(true);
  
  // Configure accelerometer for maximum sensitivity
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x1C);  // ACCEL_CONFIG register
  Wire.write(0x00);  // Â±2g range for maximum sensitivity
  Wire.endTransmission(true);
}

