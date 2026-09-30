/*
 * Solar soiling edge node (ESP32)
 * Pins: INA219 x2 on I2C (SDA 21, SCL 22; test = 0x40, reference = 0x41 via A0 bridge)
 *       DHT11 data GPIO 4, cleaning relay GPIO 26 (active HIGH)
 * MQTT: publishes sensors/test, sensors/reference, sensors/fault;
 *       listens cleaning/trigger; publishes cleaning/status (running -> done | rejected)
 *
 * Safety rules (A2):
 *  - the relay timeout is checked first in loop() and never depends on the network
 *  - Wi-Fi/MQTT reconnect is non-blocking, and is not attempted while the pump runs
 *  - duration_s is clamped to MAX_CLEAN_S; a trigger while spraying is refused
 *  - a missing INA219 is reported on sensors/fault instead of publishing zeros
 */
#include <Adafruit_INA219.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <PubSubClient.h>
#include <WiFi.h>
#include <Wire.h>

#include "secrets.h"

const int RELAY_PIN = 26;
const int MAX_CLEAN_S = 60;                    // hard ceiling, whatever the payload says
const unsigned long SENSOR_PERIOD_MS = 2000;
const unsigned long FAULT_PERIOD_MS = 10000;   // also retries ina.begin()
const unsigned long WIFI_RETRY_MS = 5000;
const unsigned long MQTT_RETRY_MS = 2000;

Adafruit_INA219 inaTest(0x40), inaRef(0x41);
bool testOk = false, refOk = false;
DHT dht(4, DHT11);
WiFiClient net;
PubSubClient mqtt(net);

long cycleId = -1;            // cycle currently spraying, -1 = idle
unsigned long cleanUntil = 0;
long pendingDoneId = -1;      // "done" that could not be sent yet (link was down)

void publishJson(const char* topic, JsonDocument& doc) {
  if (!mqtt.connected()) return;
  char buf[160];
  size_t n = serializeJson(doc, buf, sizeof buf);
  mqtt.publish(topic, reinterpret_cast<const uint8_t*>(buf), n, false);
}

bool publishStatus(long id, const char* state) {
  if (!mqtt.connected()) return false;
  JsonDocument doc;
  doc["cycle_id"] = id;
  doc["state"] = state;
  publishJson("cleaning/status", doc);
  return true;
}

void publishFault(const char* source, const char* fault) {
  JsonDocument doc;
  doc["source"] = source;
  doc["fault"] = fault;
  publishJson("sensors/fault", doc);
}

void relayOff() { digitalWrite(RELAY_PIN, LOW); }

// Highest priority in loop(): must run even with no Wi-Fi, no MQTT, no sensors.
void checkRelayTimeout() {
  if (cycleId >= 0 && (long)(millis() - cleanUntil) >= 0) {  // rollover-safe
    relayOff();
    pendingDoneId = cycleId;
    cycleId = -1;
  }
}

void onMessage(char* topic, byte* payload, unsigned int len) {
  JsonDocument doc;
  if (deserializeJson(doc, payload, len)) return;
  long id = doc["cycle_id"] | -1L;
  int dur = doc["duration_s"] | 0;
  if (id < 0) return;
  if (cycleId >= 0) {  // already spraying
    if (id != cycleId) publishStatus(id, "rejected");  // same id = duplicate delivery: ignore
    return;
  }
  if (dur <= 0) {  // missing or invalid duration: never guess, never spray
    publishStatus(id, "rejected");
    return;
  }
  if (dur > MAX_CLEAN_S) dur = MAX_CLEAN_S;
  cycleId = id;
  cleanUntil = millis() + 1000UL * (unsigned long)dur;
  digitalWrite(RELAY_PIN, HIGH);
  publishStatus(id, "running");
}

// Returns false (and publishes nothing) if the sensor is missing.
bool publishPanel(const char* source, const char* topic, Adafruit_INA219& ina, bool& ok,
                  float t, float h) {
  if (!ok) return false;
  JsonDocument doc;
  doc["v"] = ina.getBusVoltage_V() + ina.getShuntVoltage_mV() / 1000.0;
  doc["i_ma"] = ina.getCurrent_mA();
  if (!isnan(t)) doc["temp"] = t;  // omit instead of sending invalid "nan" JSON
  if (!isnan(h)) doc["hum"] = h;
  publishJson(topic, doc);
  return true;
}

// Non-blocking: one short step per call, rate-limited. Skipped while the pump runs.
void maintainConnection() {
  static unsigned long lastWifi = 0, lastMqtt = 0;
  static bool wifiStarted = false;
  if (cycleId >= 0) return;  // never let a slow connect delay relay-off
  if (WiFi.status() != WL_CONNECTED) {
    if (!wifiStarted || millis() - lastWifi > WIFI_RETRY_MS) {
      wifiStarted = true;
      lastWifi = millis();
      WiFi.disconnect();
      WiFi.begin(WIFI_SSID, WIFI_PASS);
    }
    return;
  }
  if (!mqtt.connected() && millis() - lastMqtt > MQTT_RETRY_MS) {
    lastMqtt = millis();
    if (mqtt.connect("esp32-solar")) mqtt.subscribe("cleaning/trigger", 1);
  }
}

void setup() {
  pinMode(RELAY_PIN, OUTPUT);
  relayOff();
  Wire.begin(21, 22);
  testOk = inaTest.begin();
  refOk = inaRef.begin();
  dht.begin();
  mqtt.setServer(MQTT_HOST, 1883);
  mqtt.setSocketTimeout(2);  // seconds; bounds any connect attempt
  mqtt.setCallback(onMessage);
}

void loop() {
  checkRelayTimeout();
  maintainConnection();
  mqtt.loop();
  checkRelayTimeout();  // mqtt.loop() may have taken a moment

  if (pendingDoneId >= 0 && publishStatus(pendingDoneId, "done")) pendingDoneId = -1;

  static unsigned long lastSensor = 0, lastFault = 0;
  if (millis() - lastSensor > SENSOR_PERIOD_MS) {
    lastSensor = millis();
    float t = dht.readTemperature(), h = dht.readHumidity();
    publishPanel("test", "sensors/test", inaTest, testOk, t, h);
    publishPanel("reference", "sensors/reference", inaRef, refOk, t, h);
  }
  if (millis() - lastFault > FAULT_PERIOD_MS) {
    lastFault = millis();
    if (!testOk) {
      publishFault("test", "ina219_not_found");
      testOk = inaTest.begin();  // pick it up if it was plugged in late
    }
    if (!refOk) {
      publishFault("reference", "ina219_not_found");
      refOk = inaRef.begin();
    }
  }
  checkRelayTimeout();
}
