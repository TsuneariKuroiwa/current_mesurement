#include <Wire.h>
#include <Adafruit_INA219.h>
#include <Adafruit_TinyUSB.h>

Adafruit_INA219 ina219;

// タイミング制御用変数
unsigned long previousMillis = 0;
const unsigned long interval = 25; // 25ms間隔

void setup() {
  Serial.begin(9600);

  // I2Cの初期化
  Wire.begin();
  delay(1000);

  ina219.begin();
  
  // 初期時刻を記録
  previousMillis = millis();
}

void loop() {
  unsigned long currentMillis = millis();
  
  // 25ms経過した場合のみ測定・送信を実行
  if (currentMillis - previousMillis >= interval) {
    // 前回の時刻を更新（処理時間のズレを累積させないため）
    previousMillis = currentMillis;
    
    float current_mA = ina219.getCurrent_mA();
    Serial.println(current_mA); // mA単位で送信
  }
  
  // その他の処理があればここに追加可能
}
