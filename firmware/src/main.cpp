/*
 * Solar soiling edge node (ESP32)
 * Pins: INA219 x2 on I2C (SDA 21, SCL 22; test = 0x40, reference = 0x41 via A0 bridge)
 *       DHT11 data GPIO 4, cleaning relay GPIO 26 (active HIGH)
 * MQTT: publishes sensors/test, sensors/reference; listens cleaning/trigger;
 *       publishes cleaning/status (running -> done)
 */
#include <Adafruit_INA219.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <PubSubClient.h>
#include <WiFi.h>
#include <Wire.h>

#include "secrets.h"

const int RELAY_PIN = 26;

Adafruit_INA219 inaTest(0x40), inaRef(0x41);
DHT dht(4, DHT11);
WiFiClient net;
PubSubClient mqtt(net);
long cycleId = -1;
unsigned long cleanUntil = 0;

void publishJson(const char* topic, JsonDocument& doc) {
  char buf[160];
  size_t n = serializeJson(doc, buf, sizeof buf);
  mqtt.publish(topic, reinterpret_cast<const uint8_t*>(buf), n, false);
}

void publishStatus(const char* state) {
  JsonDocument doc;
  doc["cycle_id"] = cycleId;
  doc["state"] = state;
  publishJson("cleaning/status", doc);
}

void onMessage(char* topic, byte* payload, unsigned int len) {
  JsonDocument doc;
  if (deserializeJson(doc, payload, len)) return;
  cycleId = doc["cycle_id"];
  cleanUntil = millis() + 1000UL * doc["duration_s"].as<int>();
  digitalWrite(RELAY_PIN, HIGH);
  publishStatus("running");
}

void publishPanel(const char* topic, Adafruit_INA219& ina, float t, float h) {
  JsonDocument doc;
  doc["v"] = ina.getBusVoltage_V() + ina.getShuntVoltage_mV() / 1000.0;
  doc["i_ma"] = ina.getCurrent_mA();
  if (!isnan(t)) doc["temp"] = t;  // omit instead of sending invalid "nan" JSON
  if (!isnan(h)) doc["hum"] = h;
  publishJson(topic, doc);
}

void ensureConnected() {
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    while (WiFi.status() != WL_CONNECTED) delay(300);
  }
  while (!mqtt.connected()) {
    if (mqtt.connect("esp32-solar")) mqtt.subscribe("cleaning/trigger", 1);
    else delay(2000);
  }
}

void setup() {
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, LOW);
  Wire.begin(21, 22);
  inaTest.begin();
  inaRef.begin();
  dht.begin();
  mqtt.setServer(MQTT_HOST, 1883);
  mqtt.setCallback(onMessage);
}

void loop() {
  ensureConnected();
  mqtt.loop();
  static unsigned long last = 0;
  if (millis() - last > 2000) {
    last = millis();
    float t = dht.readTemperature(), h = dht.readHumidity();
    publishPanel("sensors/test", inaTest, t, h);
    publishPanel("sensors/reference", inaRef, t, h);
  }
  if (cycleId >= 0 && millis() > cleanUntil) {
    digitalWrite(RELAY_PIN, LOW);
    publishStatus("done");
    cycleId = -1;
  }
}
