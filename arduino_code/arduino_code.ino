#include <IRremote2.h>
#include <Wire.h>

// IR Receiver
const int IR_PIN = 7;
IRrecv irrecv(IR_PIN);
decode_results results;

// Pin Definitions
// L298N Pins (Pitch Stepper)
const int PITCH_IN1 = 13;
const int PITCH_IN2 = 12;
const int PITCH_IN3 = 11;
const int PITCH_IN4 = 10;
const int PITCH_RELAY = 6;

// TB6600 Pins (Yaw Stepper)
const int YAW_PUL_PLUS = 4;
const int YAW_PUL_MINUS = 5;
const int YAW_RELAY = 8;

// MPU6050 settings
const int MPU_ADDR = 0x68;
const int PWR_MGMT_1 = 0x6B;

// Stepper sequence for L298N (full stepping)
const int STEP_COUNT = 4;
const int PITCH_SEQUENCE[4][4] = {
  {1, 0, 0, 0},
  {0, 1, 0, 0},
  {0, 0, 1, 0},
  {0, 0, 0, 1}
};

// Movement parameters - CRANKED UP!
const int STEP_DELAY = 5;        // Increased delay for more torque (ms)
const int YAW_PULSE_WIDTH = 100;  // Increased pulse width (microseconds)
const int STEPS_PER_PRESS = 200;  // More steps per button press!

// For continuous movement while button is held
const int CONTINUOUS_STEPS = 50;  // Steps per loop when holding button
unsigned long lastCommandTime = 0;
const int COMMAND_TIMEOUT = 250;  // Time in ms to consider command "finished"

// State variables
float homeOffset = 0;
bool isHomed = false;
int currentStep = 0;
bool debugMode = true;

// Last command tracking for continuous movement
unsigned long lastIRCode = 0;

void setup() {
  Serial.begin(9600);
  while (!Serial && millis() < 5000); // Wait for serial for debug
  Serial.println("Binocular Control Starting...");
  
  // Initialize pins
  pinMode(PITCH_IN1, OUTPUT);
  pinMode(PITCH_IN2, OUTPUT);
  pinMode(PITCH_IN3, OUTPUT);
  pinMode(PITCH_IN4, OUTPUT);
  pinMode(PITCH_RELAY, OUTPUT);
  
  pinMode(YAW_PUL_PLUS, OUTPUT);
  pinMode(YAW_PUL_MINUS, OUTPUT);
  pinMode(YAW_RELAY, OUTPUT);
  
  // Set initial relay states (enable motors)
  digitalWrite(PITCH_RELAY, HIGH);
  digitalWrite(YAW_RELAY, HIGH);
  
  // Initialize I2C for MPU6050
  Wire.begin();
  initMPU6050();
  
  // Start IR receiver
  irrecv.enableIRIn();
  Serial.println("IR Receiver Enabled");
  
  Serial.println("Setup Complete!");
  printControls();
}

void loop() {
  static unsigned long lastMoveTime = 0;
  static bool isMoving = false;
  
  // Check for IR commands
  if (irrecv.decode(&results)) {
    lastCommandTime = millis();
    lastIRCode = results.value;
    isMoving = true;
    
    if(debugMode) {
      Serial.print("IR Code Received: 0x");
      Serial.println(results.value, HEX);
    }
    handleCommand(results.value);
    irrecv.resume();
  }
  
  // Continue movement if button is held
  if (isMoving && (millis() - lastCommandTime < COMMAND_TIMEOUT)) {
    switch(lastIRCode) {
      case 0xFF18E7: // Button 2
        movePitchStepper(CONTINUOUS_STEPS, true);
        break;
      case 0xFF4AB5: // Button 8
        movePitchStepper(CONTINUOUS_STEPS, false);
        break;
      case 0xFF10EF: // Button 4
        moveYawStepper(CONTINUOUS_STEPS, true);
        break;
      case 0xFF5AA5: // Button 6
        moveYawStepper(CONTINUOUS_STEPS, false);
        break;
    }
  } else {
    isMoving = false;
  }
  
  // Check for Serial commands
  if (Serial.available() > 0) {
    handleSerialCommand(Serial.read());
  }
}

void handleCommand(unsigned long value) {
  switch(value) {
    case 0xFF18E7: // Button 2
      movePitchStepper(STEPS_PER_PRESS, true);
      if(debugMode) Serial.println("Moving Up");
      break;
      
    case 0xFF4AB5: // Button 8
      movePitchStepper(STEPS_PER_PRESS, false);
      if(debugMode) Serial.println("Moving Down");
      break;
      
    case 0xFF10EF: // Button 4
      moveYawStepper(STEPS_PER_PRESS, true);
      if(debugMode) Serial.println("Moving Left");
      break;
      
    case 0xFF5AA5: // Button 6
      moveYawStepper(STEPS_PER_PRESS, false);
      if(debugMode) Serial.println("Moving Right");
      break;
      
    case 0xFF38C7: // Button 5
      resetToHome();
      if(debugMode) Serial.println("Resetting to Home");
      break;
  }
}

void handleSerialCommand(char cmd) {
  switch(cmd) {
    case '2':
      movePitchStepper(STEPS_PER_PRESS, true);
      break;
    case '8':
      movePitchStepper(STEPS_PER_PRESS, false);
      break;
    case '4':
      moveYawStepper(STEPS_PER_PRESS, true);
      break;
    case '6':
      moveYawStepper(STEPS_PER_PRESS, false);
      break;
    case '5':
      resetToHome();
      break;
    case 't':
      runTestSequence();
      break;
    case 'd':
      debugMode = !debugMode;
      Serial.print("Debug mode: ");
      Serial.println(debugMode ? "ON" : "OFF");
      break;
  }
}

void movePitchStepper(int steps, bool clockwise) {
  if(debugMode) {
    Serial.print("Moving pitch ");
    Serial.print(clockwise ? "up" : "down");
    Serial.print(" by ");
    Serial.print(steps);
    Serial.println(" steps");
  }
  
  for(int i = 0; i < steps; i++) {
    if(clockwise) {
      currentStep = (currentStep + 1) % STEP_COUNT;
    } else {
      currentStep = (currentStep - 1 + STEP_COUNT) % STEP_COUNT;
    }
    
    // Energize coils with maximum power
    digitalWrite(PITCH_IN1, PITCH_SEQUENCE[currentStep][0]);
    digitalWrite(PITCH_IN2, PITCH_SEQUENCE[currentStep][1]);
    digitalWrite(PITCH_IN3, PITCH_SEQUENCE[currentStep][2]);
    digitalWrite(PITCH_IN4, PITCH_SEQUENCE[currentStep][3]);
    
    delay(STEP_DELAY);  // Longer delay for more torque
  }
  
  // Keep coils energized for holding torque
  // Only de-energize after timeout
  if (millis() - lastCommandTime > COMMAND_TIMEOUT) {
    digitalWrite(PITCH_IN1, LOW);
    digitalWrite(PITCH_IN2, LOW);
    digitalWrite(PITCH_IN3, LOW);
    digitalWrite(PITCH_IN4, LOW);
  }
}

void moveYawStepper(int steps, bool clockwise) {
  if(debugMode) {
    Serial.print("Moving yaw ");
    Serial.print(clockwise ? "left" : "right");
    Serial.print(" by ");
    Serial.print(steps);
    Serial.println(" steps");
  }
  
  for(int i = 0; i < steps; i++) {
    digitalWrite(YAW_PUL_PLUS, HIGH);
    digitalWrite(YAW_PUL_MINUS, LOW);
    delayMicroseconds(YAW_PULSE_WIDTH);
    digitalWrite(YAW_PUL_PLUS, LOW);
    digitalWrite(YAW_PUL_MINUS, HIGH);
    delayMicroseconds(YAW_PULSE_WIDTH);
  }
}

void runTestSequence() {
  Serial.println("Running test sequence...");
  
  // Test pitch movement
  Serial.println("Testing pitch up");
  movePitchStepper(STEPS_PER_PRESS, true);
  delay(1000);
  
  Serial.println("Testing pitch down");
  movePitchStepper(STEPS_PER_PRESS, false);
  delay(1000);
  
  // Test yaw movement
  Serial.println("Testing yaw left");
  moveYawStepper(STEPS_PER_PRESS, true);
  delay(1000);
  
  Serial.println("Testing yaw right");
  moveYawStepper(STEPS_PER_PRESS, false);
  delay(1000);
  
  Serial.println("Test sequence complete");
}

void resetToHome() {
  if(debugMode) Serial.println("Resetting to home position...");
  
  float currentPitch = getPitchFromMPU();
  float pitchDiff = currentPitch - homeOffset;
  
  if(debugMode) {
    Serial.print("Current pitch: ");
    Serial.print(currentPitch);
    Serial.print(" Home offset: ");
    Serial.print(homeOffset);
    Serial.print(" Difference: ");
    Serial.println(pitchDiff);
  }
  
  // Convert angle difference to steps
  int stepsNeeded = abs(pitchDiff) * 10;  // 10 steps per degree - adjust as needed
  movePitchStepper(stepsNeeded, pitchDiff > 0);
}

float getPitchFromMPU() {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);
  Wire.endTransmission(false);
  Wire.requestFrom(MPU_ADDR, 6, true);
  
  int16_t ax = Wire.read() << 8 | Wire.read();
  int16_t ay = Wire.read() << 8 | Wire.read();
  int16_t az = Wire.read() << 8 | Wire.read();
  
  float pitch = atan2(ax, sqrt(ay * ay + az * az)) * 180.0 / M_PI;
  return pitch;
}

void initMPU6050() {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(PWR_MGMT_1);
  Wire.write(0);
  Wire.endTransmission(true);
  
  delay(100);
  homeOffset = getPitchFromMPU();
  if(debugMode) {
    Serial.print("Home offset calibrated to: ");
    Serial.println(homeOffset);
  }
}

void printControls() {
  Serial.println("\nControls:");
  Serial.println("IR Remote or Serial:");
  Serial.println("2 - Move Up");
  Serial.println("8 - Move Down");
  Serial.println("4 - Move Left");
  Serial.println("6 - Move Right");
  Serial.println("5 - Reset to Home Position");
  Serial.println("\nAdditional Serial Commands:");
  Serial.println("t - Run Test Sequence");
  Serial.println("d - Toggle Debug Mode");
}