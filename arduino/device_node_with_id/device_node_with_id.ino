#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// =========================
// Пользовательские настройки
// =========================
const char *WIFI_SSID = "YOUR_WIFI_SSID";
const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char *SERVER_BASE_URL = "http://192.168.1.10:2162";

// Идентификатор звуковой точки типа 1.
const uint16_t DEVICE_ID = 102;
const uint32_t COMMAND_POLL_INTERVAL_MS = 300;
const uint32_t HEARTBEAT_INTERVAL_MS = 5000;
const char *BOARD_TYPE = "type1_sound";
const char *FIRMWARE_NAME = "device_node_with_id";

// Пины платы. При необходимости поменяйте.
const int STATUS_LED_PIN = 2;
const int BUZZER_PIN = 12;

constexpr uint8_t TONE_CHANNEL = 0;

double lastCommandUpdatedAt = -1.0;
uint32_t lastCommandPollAt = 0;
uint32_t lastHeartbeatAt = 0;
uint32_t toneStopAt = 0;
bool buzzerActive = false;
uint16_t currentFrequencyHz = 0;

void connectToWifi();
void pollCommandIfNeeded();
void postHeartbeatIfNeeded();
void applyCommand(const String &payload);
void stopToneIfNeeded();
String buildCommandUrl();
String buildHeartbeatUrl();

void setup() {
  Serial.begin(115200);
  Serial.println();
  Serial.println("Sound node with device_id starting...");

  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(STATUS_LED_PIN, LOW);

  pinMode(BUZZER_PIN, OUTPUT);
  ledcSetup(TONE_CHANNEL, 2000, 8);
  ledcAttachPin(BUZZER_PIN, TONE_CHANNEL);
  ledcWriteTone(TONE_CHANNEL, 0);

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  connectToWifi();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectToWifi();
  }

  pollCommandIfNeeded();
  postHeartbeatIfNeeded();
  stopToneIfNeeded();
}

void connectToWifi() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }

  Serial.printf("Connecting to WiFi: %s\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  uint32_t startedAt = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    if (millis() - startedAt > 15000) {
      Serial.println("\nWiFi connect timeout, retrying...");
      WiFi.disconnect(true, true);
      delay(1000);
      WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
      startedAt = millis();
    }
  }

  Serial.printf("\nWiFi connected, IP: %s\n", WiFi.localIP().toString().c_str());
}

String buildCommandUrl() {
  return String(SERVER_BASE_URL) + "/devices/type1/" + String(DEVICE_ID) + "/command";
}

String buildHeartbeatUrl() {
  return String(SERVER_BASE_URL) + "/devices/heartbeat";
}

void pollCommandIfNeeded() {
  if (millis() - lastCommandPollAt < COMMAND_POLL_INTERVAL_MS) {
    return;
  }
  lastCommandPollAt = millis();

  String url = buildCommandUrl();
  HTTPClient http;
  WiFiClient client;

  if (!http.begin(client, url)) {
    Serial.println("HTTP begin failed.");
    return;
  }

  int code = http.GET();
  if (code == 200) {
    String response = http.getString();
    applyCommand(response);
  } else if (code > 0) {
    Serial.printf("Command GET returned code %d\n", code);
  } else {
    Serial.printf("Command GET failed: %s\n", http.errorToString(code).c_str());
  }

  http.end();
}

void postHeartbeatIfNeeded() {
  if (millis() - lastHeartbeatAt < HEARTBEAT_INTERVAL_MS) {
    return;
  }
  lastHeartbeatAt = millis();

  String url = buildHeartbeatUrl();
  HTTPClient http;
  WiFiClient client;

  if (!http.begin(client, url)) {
    Serial.println("Heartbeat HTTP begin failed.");
    return;
  }

  http.addHeader("Content-Type", "application/json");

  DynamicJsonDocument doc(512);
  doc["board_type"] = BOARD_TYPE;
  doc["device_id"] = DEVICE_ID;
  doc["firmware"] = FIRMWARE_NAME;
  doc["ip_address"] = WiFi.localIP().toString();
  doc["mac_address"] = WiFi.macAddress();
  doc["wifi_rssi"] = WiFi.RSSI();
  doc["free_heap"] = ESP.getFreeHeap();
  doc["free_psram"] = ESP.getFreePsram();
  doc["uptime_seconds"] = millis() / 1000;
  JsonObject extra = doc["extra"].to<JsonObject>();
  extra["hardware"] = "ledc_pwm";
  extra["command_poll_interval_ms"] = COMMAND_POLL_INTERVAL_MS;

  String body;
  serializeJson(doc, body);

  int code = http.POST(body);
  if (code == 200) {
    Serial.printf("Heartbeat sent for %s:%u\n", BOARD_TYPE, DEVICE_ID);
  } else if (code > 0) {
    Serial.printf("Heartbeat POST returned code %d\n", code);
  } else {
    Serial.printf("Heartbeat POST failed: %s\n", http.errorToString(code).c_str());
  }

  http.end();
}

void applyCommand(const String &payload) {
  DynamicJsonDocument doc(1024);
  DeserializationError err = deserializeJson(doc, payload);
  if (err) {
    Serial.printf("Command JSON parse error: %s\n", err.c_str());
    return;
  }

  double updatedAt = doc["updated_at"] | 0.0;
  if (updatedAt == lastCommandUpdatedAt) {
    return;
  }
  lastCommandUpdatedAt = updatedAt;

  bool active = doc["active"] | false;
  uint16_t frequencyHz = doc["payload"]["frequency_hz"] | 1800;
  uint32_t durationMs = doc["payload"]["duration_ms"] | 1200;
  const char *reason = doc["reason"] | "";

  if (!active) {
    buzzerActive = false;
    currentFrequencyHz = 0;
    toneStopAt = 0;
    ledcWriteTone(TONE_CHANNEL, 0);
    digitalWrite(STATUS_LED_PIN, LOW);
    Serial.printf("Sound command OFF, reason=%s\n", reason);
    return;
  }

  buzzerActive = true;
  currentFrequencyHz = frequencyHz;
  toneStopAt = millis() + durationMs;

  ledcWriteTone(TONE_CHANNEL, frequencyHz);
  digitalWrite(STATUS_LED_PIN, HIGH);

  Serial.printf(
    "Sound command ON, device_id=%u, freq=%uHz, duration=%lu ms, reason=%s\n",
    DEVICE_ID,
    frequencyHz,
    static_cast<unsigned long>(durationMs),
    reason
  );
}

void stopToneIfNeeded() {
  if (!buzzerActive || toneStopAt == 0) {
    return;
  }

  if (millis() >= toneStopAt) {
    buzzerActive = false;
    currentFrequencyHz = 0;
    toneStopAt = 0;
    ledcWriteTone(TONE_CHANNEL, 0);
    digitalWrite(STATUS_LED_PIN, LOW);
    Serial.println("Sound timeout reached -> buzzer OFF");
  }
}
