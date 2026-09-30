/*
 * ESP32 soiling node. Pins: I2C SDA 21 / SCL 22 (INA219 test @0x40, reference @0x41 with A0 bridged),
 * DHT11 data GPIO 4, relay input GPIO 26 (pump/wiper).
 * MQTT contract: publishes sensors/test + sensors/reference, listens on cleaning/trigger,
 * reports cleaning/status ("running", then "done").
 */
#include <WiFi.h>
#include <PubSubClient.h>
#include <Adafruit_INA219.h>
#include <DHT.h>
#include <ArduinoJson.h>
#include "secrets.h"

const int RELAY_PIN = 26;

Adafruit_INA219 inaTest(0x40), inaRef(0x41);
DHT dht(4, DHT11);
WiFiClient net;
PubSubClient mqtt(net);
long cycleId = -1;
unsigned long cleanUntil = 0;

void onMessage(char* topic, byte* payload, unsigned int len) {
  JsonDocument doc;
  if (deserializeJson(doc, payload, len)) return;
  cycleId = doc["cycle_id"];
  cleanUntil = millis() + 1000UL * doc["duration_s"].as<int>();
  digitalWrite(RELAY_PIN, HIGH);
  char b[64];
  snprintf(b, sizeof b, "{\"cycle_id\":%ld,\"state\":\"running\"}", cycleId);
  mqtt.publish("cleaning/status", b);
}

void publishPanel(const char* topic, Adafruit_INA219& ina, float t, float h) {
  float v = ina.getBusVoltage_V() + ina.getShuntVoltage_mV() / 1000.0;
  char b[128];
  snprintf(b, sizeof b, "{\"v\":%.2f,\"i_ma\":%.1f,\"temp\":%.1f,\"hum\":%.1f}", v, ina.getCurrent_mA(), t, h);
  mqtt.publish(topic, b);
}

void ensureConnected() {
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    while (WiFi.status() != WL_CONNECTED) delay(300);
  }
  while (!mqtt.connected()) {
    bool ok = (strlen(MQTT_USER) > 0) ? mqtt.connect("esp32-solar", MQTT_USER, MQTT_PASS)
                                       : mqtt.connect("esp32-solar");
    if (ok) mqtt.subscribe("cleaning/trigger", 1);
    else delay(2000);
  }
}

void setup() {
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, LOW);
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
    if (isnan(t)) t = 0;  // DHT11 occasionally fails a read; keep the JSON valid
    if (isnan(h)) h = 0;
    publishPanel("sensors/test", inaTest, t, h);
    publishPanel("sensors/reference", inaRef, t, h);
  }
  if (cycleId >= 0 && millis() > cleanUntil) {
    digitalWrite(RELAY_PIN, LOW);
    char b[64];
    snprintf(b, sizeof b, "{\"cycle_id\":%ld,\"state\":\"done\"}", cycleId);
    mqtt.publish("cleaning/status", b);
    cycleId = -1;
  }
}
