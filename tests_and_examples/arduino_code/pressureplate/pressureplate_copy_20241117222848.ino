#define PRESSURE_SENSOR_1_PIN A0
#define PRESSURE_SENSOR_2_PIN A1
#define MOTOR_ENABLE_PIN 4    // Pin controlling motor enable (adjust as needed)

const int threshold = 650;

void setup() {
  Serial.begin(9600);

  pinMode(MOTOR_ENABLE_PIN, OUTPUT);
  digitalWrite(MOTOR_ENABLE_PIN, LOW); // Initially enable motors

  // Other setup code for your motors and sensors
}

void loop() {
  int sensorValue1 = analogRead(PRESSURE_SENSOR_1_PIN);
  int sensorValue2 = analogRead(PRESSURE_SENSOR_2_PIN);

  Serial.print("Sensor 1 Value: ");
  Serial.println(sensorValue1);
  Serial.print("Sensor 2 Value: ");
  Serial.println(sensorValue2);

  if (sensorValue1 > threshold || sensorValue2 > threshold) {
    // Deactivate motors
    digitalWrite(MOTOR_ENABLE_PIN, LOW); // Assuming HIGH disables the motors
  } else {
    // Activate motors
    digitalWrite(MOTOR_ENABLE_PIN, HIGH);  // Assuming LOW enables the motors
  }
  Serial.print("pressure trigger: ");
  Serial.println(digitalRead(8));
  // Rest of your code (e.g., controlling steppers, reading other sensors)

  // Optional delay
  delay(100);
}
