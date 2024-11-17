// MultiStepper.pde
// -*- mode: C++ -*-
//
// Shows how to multiple simultaneous steppers
// Runs one stepper forwards and backwards, accelerating and decelerating
// at the limits. Runs other steppers at the same time
//
// Copyright (C) 2009 Mike McCauley
// $Id: MultiStepper.pde,v 1.1 2011/01/05 01:51:01 mikem Exp mikem $
 
#include <AccelStepper.h>
 
// L298N Pins (Pitch Stepper)
const int PITCH_IN1 = 13;
const int PITCH_IN2 = 11;
const int PITCH_IN3 = 12;
const int PITCH_IN4 = 10;

// TB6600 Pins (Yaw Stepper)
const int YAW_PUL = 4;
const int YAW_DIR = 5;

AccelStepper stepper1(AccelStepper::DRIVER, YAW_PUL, YAW_DIR);
 
void setup()
{  
    stepper1.setMaxSpeed(1000.0);
    stepper1.setAcceleration(600.0);
    stepper1.moveTo(200);
    
    // stepper2.setMaxSpeed(300.0);
    // stepper2.setAcceleration(100.0);
    // stepper2.moveTo(1000000);
    // 
    // stepper3.setMaxSpeed(300.0);
    // stepper3.setAcceleration(100.0);
    // stepper3.moveTo(1000000); 
}
 
void loop()
{
    // Change direction at the limits
    if (stepper1.distanceToGo() == 0)
        stepper1.moveTo(-stepper1.currentPosition());
    stepper1.run();
}
