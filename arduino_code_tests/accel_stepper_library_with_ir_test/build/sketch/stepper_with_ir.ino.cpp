#include <Arduino.h>
#line 1 "/home/lucienfradet/Arduino/stepper_with_ir/stepper_with_ir.ino"
#include <AccelStepper.h>
#include <MultiStepper.h>
#include <IRremote.h>
#include <Wire.h>

// IR Receiver
const int IR_PIN = 7;
IRrecv irrecv(IR_PIN);
decode_results results;

// L298N Pins (Pitch Stepper)
const int PITCH_IN1 = 13;
const int PITCH_IN2 = 11;
const int PITCH_IN3 = 12;
const int PITCH_IN4 = 10;
const int PITCH_RELAY = 6;

// TB6600 Pins (Yaw Stepper)
const int YAW_PUL = 4;
const int YAW_DIR = 5;
const int YAW_RELAY = 8;

AccelStepper stepperYaw(AccelStepper::DRIVER, YAW_PUL, YAW_DIR);
AccelStepper stepperTilt(AccelStepper::FULL4WIRE, PITCH_IN1, PITCH_IN2, PITCH_IN3, PITCH_IN4);
MultiStepper steppers;

long tiltCurrentPosition = 0; // Track the current position of the tilt motor
const long TILT_MIN = -70;     // Minimum position for tilt motor
const long TILT_MAX = 70;      // Maximum position for tilt motor

#line 31 "/home/lucienfradet/Arduino/stepper_with_ir/stepper_with_ir.ino"
void setup();
#line 47 "/home/lucienfradet/Arduino/stepper_with_ir/stepper_with_ir.ino"
void handleCommand(unsigned long value);
#line 79 "/home/lucienfradet/Arduino/stepper_with_ir/stepper_with_ir.ino"
void loop();
#line 31 "/home/lucienfradet/Arduino/stepper_with_ir/stepper_with_ir.ino"
void setup() {
  stepperTilt.setMaxSpeed(50);
  stepperTilt.setAcceleration(50);
  stepperYaw.setMaxSpeed(150);
  stepperYaw.setAcceleration(100);

  // Add steppers to MultiStepper
  steppers.addStepper(stepperYaw);
  steppers.addStepper(stepperTilt);

  // Start IR receiver
  irrecv.enableIRIn();
}

long positions[2] = {0, 0};

void handleCommand(unsigned long value) {
    switch(value) {
      case 0xFF18E7: // Button 2
        if (tiltCurrentPosition < TILT_MAX) { // Check upper limit

        tiltCurrentPosition--;
        positions[1] = tiltCurrentPosition;
        steppers.moveTo(positions);
        steppers.runSpeedToPosition(); // Blocks until all are in position
        }
        break;

      case 0xFF4AB5: // Button 8
        if (tiltCurrentPosition > TILT_MIN) { // Check lower limit
          tiltCurrentPosition--;
          stepperTilt.moveTo(tiltCurrentPosition);
          stepperTilt.runSpeedToPosition(); // Move to the new position
        }
        break;

      case 0xFF10EF: // Button 4
        stepperYaw.moveTo(stepperYaw.currentPosition() - 10); // Adjust speed as needed
        stepperYaw.runSpeedToPosition();
        break;

      case 0xFF5AA5: // Button 6
        stepperYaw.moveTo(stepperYaw.currentPosition() + 10); // Adjust speed as needed
        stepperYaw.runSpeedToPosition();
        break;
    }
}

void loop() {
  // Check for IR commands
  if (irrecv.decode(&results)) {
    handleCommand(results.value);
    irrecv.resume();
  }
}

