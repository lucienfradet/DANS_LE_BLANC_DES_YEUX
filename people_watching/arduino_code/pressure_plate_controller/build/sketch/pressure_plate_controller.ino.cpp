#include <Arduino.h>
#line 1 "/home/lucienfradet/Arduino/CART461_PEOPLE_WATCHING/arduino_code/pressure_plate_controller/pressure_plate_controller.ino"
#define PRESSURE_SENSOR_1_PIN A0
#define PRESSURE_SENSOR_2_PIN A1
#define MOTOR_ENABLE_PIN 4    // Pin controlling motor enable (adjust as needed)

const int threshold = 900;

#line 7 "/home/lucienfradet/Arduino/CART461_PEOPLE_WATCHING/arduino_code/pressure_plate_controller/pressure_plate_controller.ino"
void setup();
#line 16 "/home/lucienfradet/Arduino/CART461_PEOPLE_WATCHING/arduino_code/pressure_plate_controller/pressure_plate_controller.ino"
void loop();
#line 7 "/home/lucienfradet/Arduino/CART461_PEOPLE_WATCHING/arduino_code/pressure_plate_controller/pressure_plate_controller.ino"
void setup() {
  Serial.begin(9600);

  pinMode(MOTOR_ENABLE_PIN, OUTPUT);
  digitalWrite(MOTOR_ENABLE_PIN, LOW); // Initially enable motors

  // Other setup code for your motors and sensors
}

void loop() {
  int sensorValue1 = analogRead(PRESSURE_SENSOR_1_PIN);
  int sensorValue2 = analogRead(PRESSURE_SENSOR_2_PIN);

  Serial.print("Sensors Values: ");
  Serial.print(sensorValue1);
  Serial.print(" - ");
  Serial.println(sensorValue2);

  if (sensorValue1 > threshold || sensorValue2 > threshold) {
    // Deactivate motors
    digitalWrite(MOTOR_ENABLE_PIN, HIGH); // Assuming HIGH disables the motors
  } else {
    // Activate motors
    digitalWrite(MOTOR_ENABLE_PIN, LOW);  // Assuming LOW enables the motors
  }
  Serial.print("pressure trigger: ");
  Serial.println(digitalRead(MOTOR_ENABLE_PIN));
  // Rest of your code (e.g., controlling steppers, reading other sensors)

  // Optional delay
  delay(500);
}

