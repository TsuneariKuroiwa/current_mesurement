#include <Wire.h>
#include <Adafruit_INA219.h>
#include <Adafruit_TinyUSB.h>

Adafruit_INA219 ina219;

void setup() {
  Serial.begin(9600);

  // I2Cの初期化
  Wire.begin();
  delay(1000);

  ina219.begin();
}

void loop() {
  float current_mA = ina219.getCurrent_mA();
  Serial.println(current_mA); // A単位で小数点3桁まで
  delay(25);  // 40Hzで送信
}
