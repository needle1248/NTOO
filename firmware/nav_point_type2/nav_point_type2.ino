#include <Adafruit_NeoPixel.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include "config.h"

Adafruit_NeoPixel strip(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);
unsigned long lastHeartbeat = 0;
unsigned long lastPoll = 0;

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

void setColor(uint8_t r, uint8_t g, uint8_t b, int durationMs) {
  for (int i = 0; i < LED_COUNT; ++i) {
    strip.setPixelColor(i, strip.Color(r, g, b));
  }
  strip.show();
  delay(durationMs);
  strip.clear();
  strip.show();
}

void handleCommandPayload(const String& payload) {
  int commandPos = payload.indexOf("\"command_id\":");
  if (commandPos < 0) {
    return;
  }
  int commandId = payload.substring(commandPos + 13).toInt();
  switch (commandId) {
    case 1: setColor(255, 255, 0, 1000); break;
    case 2: setColor(0, 180, 255, 1500); break;
    case 3: setColor(0, 255, 150, 1250); break;
    case 4: setColor(180, 255, 0, 1500); break;
    case 5: setColor(0, 120, 255, 2000); break;
    case 6: setColor(255, 200, 0, 1750); break;
    case 7: setColor(0, 255, 220, 1500); break;
    case 8: setColor(100, 200, 255, 2000); break;
  }
}

void pollCommands() {
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
  String payload = String("{\"type\":4,\"device_id\":\"") + DEVICE_ID + "\",\"rfid_code\":\"" + uid + "\"}";
  sendJson("/api/device/event", payload);
}

void setup() {
  Serial.begin(115200);
  strip.begin();
  strip.clear();
  strip.show();
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

