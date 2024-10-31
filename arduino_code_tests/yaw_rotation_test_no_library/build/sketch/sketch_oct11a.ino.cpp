#include <Arduino.h>
#line 1 "/home/lucienfradet/Arduino/sketch_oct11a/sketch_oct11a.ino"
/*
  Stepper Motor Test
  stepper-test01.ino
  Uses MA860H or similar Stepper Driver Unit
  Has speed control & reverse switch
  
  DroneBot Workshop 2019
  https://dronebotworkshop.com
*/

// Defin pins

int reverseSwitch = 2;  // Push button for reverse
int driverPUL = 7;    // PUL- pin
int driverDIR = 6;    // DIR- pin
int spd = A0;     // Potentiometer

// Variables

int pd = 1700;       // Pulse Delay period

// Interrupt Handler

boolean speedFlag = false;


#line 27 "/home/lucienfradet/Arduino/sketch_oct11a/sketch_oct11a.ino"
void setup();
#line 35 "/home/lucienfradet/Arduino/sketch_oct11a/sketch_oct11a.ino"
void loop();
#line 27 "/home/lucienfradet/Arduino/sketch_oct11a/sketch_oct11a.ino"
void setup() {
  Serial.begin(9600);
  pinMode (driverPUL, OUTPUT);
  pinMode (driverDIR, OUTPUT);
  digitalWrite(driverDIR, HIGH);
  
}

void loop() {
  // Control motor speed in code
  if (!speedFlag) {
    pd = pd - 1;  // Gradually decrease the delay, making the motor go faster

    if (pd < 500) {
      speedFlag = true;
    }
  }

  if (speedFlag) {
    pd = pd + 1;  // Gradually decrease the delay, making the motor go faster

    if (pd > 1700) {
      speedFlag = false;
      digitalWrite(driverDIR, !digitalRead(driverDIR));
    }
  }

  // map(pd, 0,1023, 2000, 50);



  digitalWrite(driverPUL,HIGH);
  delayMicroseconds(pd);
  digitalWrite(driverPUL,LOW);
  delayMicroseconds(pd);
}

