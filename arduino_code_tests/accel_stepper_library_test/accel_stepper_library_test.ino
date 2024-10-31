// Overshoot.pde
// -*- mode: C++ -*-
//
// Check overshoot handling
// which sets a new target position and then waits until the stepper has 
// achieved it. This is used for testing the handling of overshoots
//
// Copyright (C) 2009 Mike McCauley
// $Id: Overshoot.pde,v 1.1 2011/01/05 01:51:01 mikem Exp mikem $

#include <AccelStepper.h>
#include <MultiStepper.h>


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
//
// Up to 10 steppers can be handled as a group by MultiStepper
MultiStepper steppers;

void setup()
{  
  stepperTilt.setMaxSpeed(50);
  stepperTilt.setAcceleration(50);

  stepperYaw.setMaxSpeed(150);
  stepperYaw.setAcceleration(100);

  // Then give them to MultiStepper to manage
  steppers.addStepper(stepperYaw);
  steppers.addStepper(stepperTilt);
}

void loop()
{    
  // Move yaw back and forth
  // stepperYaw.moveTo(500);
  // while (stepperYaw.currentPosition() != 300) // Full speed up to 300
  //   stepperYaw.run();
  // stepperYaw.runToNewPosition(0); // Cause an overshoot then back to 0

  // Move tilt back and forth
  // stepperTilt.moveTo(24);
  // while (stepperTilt.currentPosition() != 50) // Full speed up to 300
  //   stepperTilt.run();
  // stepperTilt.runToNewPosition(0); // Cause an overshoot then back to 0
  // stepperTilt.moveTo(70);
  // stepperTilt.runToNewPosition(-70); // Move to -24
  // stepperTilt.runToNewPosition(70);  // Move back to 24
  long positions[2]; // Array of desired stepper positions

  positions[0] = 500;
  positions[1] = 70;
  steppers.moveTo(positions);
  steppers.runSpeedToPosition(); // Blocks until all are in position

  positions[0] = -500;
  positions[1] = -70;
  steppers.moveTo(positions);
  steppers.runSpeedToPosition(); // Blocks until all are in position
}
