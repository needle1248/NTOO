#include <HTTPClient.h>
#include <WiFi.h>
#include "config.h"

unsigned long lastHeartbeat = 0;
unsigned long lastPoll = 0;
unsigned long lastSensor = 0;
float filteredDistance = 999.0;

void ensureWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) {
    delay(300);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected");
}

void sendJson(const String& path, const String& payload) {
  HTTPClient http;
  http.begin(String(LOCAL_SERVER_BASE) + path);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(payload);
  Serial.printf("POST %s => %d\n", path.c_str(), code);
  http.end();
}

void sendHeartbeat() {
  String payload = String("{\"device_id\":\"") + DEVICE_ID + "\",\"device_kind\":\"" + DEVICE_KIND + "\",\"status\":\"ok\"}";
  sendJson("/api/device/heartbeat", payload);
}

float readDistanceCm() {
  int raw = analogRead(DISTANCE_PIN);
  return raw > 0 ? 4000.0 / raw : 999.0;
}

void sendDistanceMetric(float distanceCm) {
  String payload = String("{\"type\":99,\"device_id\":\"") + DEVICE_ID + "\",\"distance_cm\":" + String(distanceCm, 1) + "}";
  sendJson("/api/device/event", payload);
}

void setVibration(bool enabled) {
  digitalWrite(VIBRO_PIN, enabled ? HIGH : LOW);
  Serial.printf("Vibration %s\n", enabled ? "ON" : "OFF");
}

void handleCommandPayload(const String& payload) {
  if (payload.indexOf("\"state\":\"on\"") >= 0) {
    setVibration(true);
  } else if (payload.indexOf("\"state\":\"off\"") >= 0) {
    setVibration(false);
  }
}

void pollCommands() {
  HTTPClient http;
  http.begin(String(LOCAL_SERVER_BASE) + "/api/device/commands/" + DEVICE_ID);
  int code = http.GET();
  if (code == 200) {
    handleCommandPayload(http.getString());
  }
  http.end();
}

void setup() {
  Serial.begin(115200);
  pinMode(VIBRO_PIN, OUTPUT);
  pinMode(DISTANCE_PIN, INPUT);
  ensureWiFi();
}

void loop() {
  ensureWiFi();
  if (millis() - lastHeartbeat >= HEARTBEAT_INTERVAL_MS) {
    sendHeartbeat();
    lastHeartbeat = millis();
  }
  if (millis() - lastPoll >= COMMAND_POLL_INTERVAL_MS) {
    pollCommands();
    lastPoll = millis();
  }
  if (millis() - lastSensor >= SENSOR_INTERVAL_MS) {
    float current = readDistanceCm();
    filteredDistance = 0.8f * filteredDistance + 0.2f * current;
    if (filteredDistance < DETECT_THRESHOLD_CM || current < DETECT_THRESHOLD_CM) {
      sendDistanceMetric(filteredDistance);
    }
    lastSensor = millis();
  }
  delay(50);
}
