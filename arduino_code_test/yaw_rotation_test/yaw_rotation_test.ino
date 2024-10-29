/*
  Stepper Motor Test
  stepper-test01.ino
  Uses MA860H or similar Stepper Driver Unit
  Has speed control & reverse switch
  
  DroneBot Workshop 2019
  https://dronebotworkshop.com
*/

// Defin pins

int driverPUL = 7;    // PUL- pin
int driverDIR = 6;    // DIR- pin

// Variables

int pd = 1700;       // Pulse Delay period

// Interrupt Handler

boolean speedFlag = false;


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
