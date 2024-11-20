#define PRESSURE_SENSOR_PIN A0
#define MOTOR_ENABLE_PIN 8    // Pin controlling motor enable (adjust as needed)

const int threshold = 250;

void setup() {
  Serial.begin(9600);

  pinMode(MOTOR_ENABLE_PIN, OUTPUT);
  digitalWrite(MOTOR_ENABLE_PIN, LOW); // Initially enable motors

  // Other setup code for your motors and sensors
}

void loop() {
  int sensorValue = analogRead(PRESSURE_SENSOR_PIN);

  Serial.print("Sensor Value: ");
  Serial.println(sensorValue);

  if (sensorValue > threshold) {
    // Deactivate motors
    digitalWrite(MOTOR_ENABLE_PIN, HIGH); // Assuming HIGH disables the motors
  } else {
    // Activate motors
    digitalWrite(MOTOR_ENABLE_PIN, LOW);  // Assuming LOW enables the motors
  }
  Serial.println(digitalRead(8));
  // Rest of your code (e.g., controlling steppers, reading other sensors)

  // Optional delay
  delay(100);
}
