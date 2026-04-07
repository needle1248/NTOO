#include <HTTPClient.h>
#include <SPI.h>
#include <WiFi.h>
#include "config.h"

unsigned long lastHeartbeat = 0;
unsigned long lastPoll = 0;
unsigned long lastRfidMillis = 0;
String lastRfid = "";

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
  if (WiFi.status() != WL_CONNECTED) {
    return;
  }
  HTTPClient http;
  http.begin(String(LOCAL_SERVER_BASE) + path);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(payload);
  Serial.printf("POST %s => %d\n", path.c_str(), code);
  http.end();
}

void sendHeartbeat() {
  String payload = String("{\"device_id\":\"") + DEVICE_ID + "\","
    + "\"device_kind\":\"" + DEVICE_KIND + "\","
    + "\"status\":\"ok\","
    + "\"last_seen\":" + "\"" + String(millis()) + "\""
    + "}";
  sendJson("/api/device/heartbeat", payload);
}

void buzz(int frequencyHz, int durationMs) {
  tone(BUZZER_PIN, frequencyHz, durationMs);
  delay(durationMs);
  noTone(BUZZER_PIN);
  Serial.printf("Buzz %d Hz for %d ms\n", frequencyHz, durationMs);
}

void handleCommandPayload(const String& payload) {
  int commandPos = payload.indexOf("\"command_id\":");
  if (commandPos < 0) {
    return;
  }
  int commandId = payload.substring(commandPos + 13).toInt();
  int frequencyMap[] = {0, 100, 300, 500, 700, 900, 1100, 1300, 1500};
  if (commandId >= 1 && commandId <= 8) {
    buzz(frequencyMap[commandId], 1000);
  }
}

void pollCommands() {
  if (WiFi.status() != WL_CONNECTED) {
    return;
  }
  HTTPClient http;
  http.begin(String(LOCAL_SERVER_BASE) + "/api/device/commands/" + DEVICE_ID);
  int code = http.GET();
  if (code == 200) {
    String payload = http.getString();
    handleCommandPayload(payload);
  }
  http.end();
}

String readRfidUid() {
  // Replace with MFRC522 read in production.
  return "";
}

void maybeSendRfidEvent() {
  String uid = readRfidUid();
  if (uid.length() == 0) {
    return;
  }
  if (uid == lastRfid && millis() - lastRfidMillis < DUPLICATE_SUPPRESS_MS) {
    return;
  }
  lastRfid = uid;
  lastRfidMillis = millis();
  String payload = String("{\"type\":4,\"device_id\":\"") + DEVICE_ID + "\",\"rfid_code\":\"" + uid + "\"}";
  sendJson("/api/device/event", payload);
  Serial.printf("RFID event: %s\n", uid.c_str());
}

void setup() {
  Serial.begin(115200);
  pinMode(BUZZER_PIN, OUTPUT);
  SPI.begin();
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
  maybeSendRfidEvent();
  delay(50);
}

