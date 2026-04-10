#include <Arduino.h>
#include <WiFi.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_MCP4725.h>
#include <VL53L0X.h>

const char *WIFI_SSID = "arsIk";
const char *WIFI_PASSWORD = "admin54421";

// Safer than parsing a URL string on each request.
const char *SERVER_HOST = "10.251.115.95";
const uint16_t SERVER_PORT = 2162;

// Must match route action.node_id for this board.
const uint16_t NODE_ID = 103;

const uint32_t COMMAND_POLL_INTERVAL_MS = 700;
const uint32_t HEARTBEAT_INTERVAL_MS = 5000;
const char *BOARD_TYPE = "type1_sound";
const char *FIRMWARE_NAME = "device_node_mcp4725_vl53l0x";

const int STATUS_LED_PIN = 2;
const uint8_t I2C_SDA_PIN = 21;
const uint8_t I2C_SCL_PIN = 22;
const uint8_t MCP4725_ADDRESS = 0x60;

const uint8_t RANGE_LED_PIN_1 = 14;
const uint8_t RANGE_LED_PIN_2 = 15;
const uint32_t DISTANCE_POLL_INTERVAL_MS = 100;
const uint16_t DISTANCE_TRIGGER_MM = 500;

const uint16_t DAC_HIGH = 1000;
const uint16_t DAC_LOW = 100;

Adafruit_MCP4725 buzzer;
VL53L0X lox;
WiFiClient commandClient;

double lastCommandUpdatedAt = -1.0;
uint32_t lastCommandPollAt = 0;
uint32_t lastWifiRetryAt = 0;
uint32_t lastHeartbeatAt = 0;
uint32_t lastDistancePollAt = 0;
uint16_t currentDistanceMm = 0;
bool currentDetected = false;
bool hasDistanceMeasurement = false;
bool distanceSensorReady = false;

void connectToWifi();
void pollCommandIfNeeded();
void postHeartbeatIfNeeded();
void pollDistanceIfNeeded();
void initDistanceSensor();
bool fetchAndApplyCommand();
bool sendHeartbeat();
bool readHttpStatusOk(WiFiClient &client);
void skipHttpHeaders(WiFiClient &client);
bool readLine(WiFiClient &client, char *buffer, size_t bufferSize);
void logCommandSummary(JsonDocument &doc);
void applyCommand(JsonDocument &doc);
void stopSound();
void playMelody(JsonArrayConst melody);
void playFrequency(uint16_t frequencyHz, uint32_t durationMs);
void playNoteType(int toneType, uint32_t durationMs);
uint16_t toneDelayMicrosForType(int toneType);
void setRangeIndicator(bool detected);

void setup() {
  Serial.begin(115200);
  Serial.println();
  Serial.println("MCP4725 route sound node with VL53L0X starting...");

  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(STATUS_LED_PIN, LOW);

  pinMode(RANGE_LED_PIN_1, OUTPUT);
  pinMode(RANGE_LED_PIN_2, OUTPUT);
  setRangeIndicator(false);

  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  if (!buzzer.begin(MCP4725_ADDRESS)) {
    Serial.println("MCP4725 init failed.");
  }
  stopSound();
  initDistanceSensor();

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  connectToWifi();
}

void loop() {
  pollDistanceIfNeeded();

  if (WiFi.status() != WL_CONNECTED) {
    if (millis() - lastWifiRetryAt > 2000) {
      lastWifiRetryAt = millis();
      connectToWifi();
    }
    delay(20);
    return;
  }

  pollCommandIfNeeded();
  postHeartbeatIfNeeded();
  delay(10);
}

void connectToWifi() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }

  Serial.printf("Connecting to WiFi: %s\n", WIFI_SSID);
  WiFi.disconnect(true, true);
  delay(300);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  uint32_t startedAt = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    if (millis() - startedAt > 15000) {
      Serial.println("\nWiFi connect timeout.");
      return;
    }
  }

  Serial.printf("\nWiFi connected, IP: %s\n", WiFi.localIP().toString().c_str());
}

void initDistanceSensor() {
  if (!lox.init()) {
    distanceSensorReady = false;
    Serial.println("VL53L0X not found. Sound node will continue without distance sensor.");
    return;
  }

  lox.setTimeout(500);
  distanceSensorReady = true;
  Serial.println("VL53L0X init OK");
}

void pollCommandIfNeeded() {
  if (millis() - lastCommandPollAt < COMMAND_POLL_INTERVAL_MS) {
    return;
  }
  lastCommandPollAt = millis();
  Serial.printf("Polling /devices/type1/%u/command\n", NODE_ID);

  if (!fetchAndApplyCommand()) {
    commandClient.stop();
  }
}

void postHeartbeatIfNeeded() {
  if (millis() - lastHeartbeatAt < HEARTBEAT_INTERVAL_MS) {
    return;
  }
  lastHeartbeatAt = millis();
  sendHeartbeat();
}

void pollDistanceIfNeeded() {
  if (!distanceSensorReady) {
    return;
  }
  if (millis() - lastDistancePollAt < DISTANCE_POLL_INTERVAL_MS) {
    return;
  }
  lastDistancePollAt = millis();

  uint16_t distanceMm = lox.readRangeSingleMillimeters();
  if (lox.timeoutOccurred()) {
    Serial.println("VL53L0X timeout.");
    currentDistanceMm = 0;
    currentDetected = false;
    hasDistanceMeasurement = true;
    setRangeIndicator(false);
    return;
  }

  bool detected = distanceMm < DISTANCE_TRIGGER_MM;
  currentDistanceMm = distanceMm;
  currentDetected = detected;
  hasDistanceMeasurement = true;
  setRangeIndicator(detected);

  Serial.printf(
    "Distance = %u mm, detected=%s\n",
    distanceMm,
    detected ? "true" : "false"
  );
}

bool fetchAndApplyCommand() {
  if (commandClient.connected()) {
    commandClient.stop();
    delay(5);
  }

  commandClient.setTimeout(3000);
  if (!commandClient.connect(SERVER_HOST, SERVER_PORT)) {
    Serial.printf("TCP connect failed: %s:%u\n", SERVER_HOST, SERVER_PORT);
    return false;
  }

  commandClient.printf(
    "GET /devices/type1/%u/command HTTP/1.0\r\nHost: %s\r\nConnection: close\r\nAccept: application/json\r\n\r\n",
    NODE_ID,
    SERVER_HOST
  );

  if (!readHttpStatusOk(commandClient)) {
    commandClient.stop();
    return false;
  }

  skipHttpHeaders(commandClient);

  StaticJsonDocument<4096> doc;
  DeserializationError err = deserializeJson(doc, commandClient);
  commandClient.stop();

  if (err) {
    Serial.printf("Command JSON parse error: %s\n", err.c_str());
    return false;
  }

  logCommandSummary(doc);
  applyCommand(doc);
  return true;
}

bool sendHeartbeat() {
  WiFiClient client;
  client.setTimeout(3000);
  if (!client.connect(SERVER_HOST, SERVER_PORT)) {
    Serial.printf("Heartbeat TCP connect failed: %s:%u\n", SERVER_HOST, SERVER_PORT);
    return false;
  }

  StaticJsonDocument<640> doc;
  doc["board_type"] = BOARD_TYPE;
  doc["device_id"] = NODE_ID;
  doc["firmware"] = FIRMWARE_NAME;
  doc["ip_address"] = WiFi.localIP().toString();
  doc["mac_address"] = WiFi.macAddress();
  doc["wifi_rssi"] = WiFi.RSSI();
  doc["free_heap"] = ESP.getFreeHeap();
  doc["free_psram"] = ESP.getFreePsram();
  doc["uptime_seconds"] = millis() / 1000;
  JsonObject extra = doc["extra"].to<JsonObject>();
  extra["hardware"] = "mcp4725+vl53l0x";
  extra["command_poll_interval_ms"] = COMMAND_POLL_INTERVAL_MS;
  extra["distance_sensor_ready"] = distanceSensorReady;
  extra["distance_trigger_mm"] = DISTANCE_TRIGGER_MM;
  if (hasDistanceMeasurement) {
    extra["current_distance_mm"] = currentDistanceMm;
    extra["distance_detected"] = currentDetected;
  }

  String body;
  serializeJson(doc, body);

  client.printf(
    "POST /devices/heartbeat HTTP/1.0\r\n"
    "Host: %s\r\n"
    "Content-Type: application/json\r\n"
    "Content-Length: %u\r\n"
    "Connection: close\r\n\r\n",
    SERVER_HOST,
    static_cast<unsigned>(body.length())
  );
  client.print(body);

  if (!readHttpStatusOk(client)) {
    client.stop();
    return false;
  }

  skipHttpHeaders(client);
  client.stop();
  Serial.printf("Heartbeat sent for %s:%u\n", BOARD_TYPE, NODE_ID);
  return true;
}

bool readHttpStatusOk(WiFiClient &client) {
  char line[96];
  if (!readLine(client, line, sizeof(line))) {
    Serial.println("HTTP status read failed.");
    return false;
  }

  if (strstr(line, "200") == nullptr) {
    Serial.printf("Unexpected HTTP status: %s\n", line);
    return false;
  }

  return true;
}

void skipHttpHeaders(WiFiClient &client) {
  char line[160];
  while (readLine(client, line, sizeof(line))) {
    if (line[0] == '\0') {
      return;
    }
  }
}

bool readLine(WiFiClient &client, char *buffer, size_t bufferSize) {
  if (bufferSize == 0) {
    return false;
  }

  size_t index = 0;
  uint32_t startedAt = millis();

  while (millis() - startedAt < 3000) {
    while (client.available()) {
      char c = static_cast<char>(client.read());
      startedAt = millis();

      if (c == '\r') {
        continue;
      }
      if (c == '\n') {
        buffer[index] = '\0';
        return true;
      }

      if (index + 1 < bufferSize) {
        buffer[index++] = c;
      }
    }

    if (!client.connected()) {
      break;
    }
    delay(1);
  }

  buffer[index] = '\0';
  return index > 0;
}

void logCommandSummary(JsonDocument &doc) {
  bool active = doc["active"] | false;
  double updatedAt = doc["updated_at"] | 0.0;
  const char *commandType = doc["command_type"] | "";
  uint32_t durationMs = doc["payload"]["duration_ms"] | 0;
  uint16_t frequencyHz = doc["payload"]["frequency_hz"] | 0;
  size_t melodySize = doc["payload"]["melody"].is<JsonArray>() ? doc["payload"]["melody"].size() : 0;

  Serial.printf(
    "Command received: active=%s, type=%s, updated_at=%.3f, duration_ms=%lu, frequency_hz=%u, melody_size=%u\n",
    active ? "true" : "false",
    commandType,
    updatedAt,
    static_cast<unsigned long>(durationMs),
    frequencyHz,
    static_cast<unsigned>(melodySize)
  );
}

void applyCommand(JsonDocument &doc) {
  double updatedAt = doc["updated_at"] | 0.0;
  if (updatedAt == lastCommandUpdatedAt) {
    Serial.println("Command unchanged.");
    return;
  }
  lastCommandUpdatedAt = updatedAt;

  bool active = doc["active"] | false;
  if (!active) {
    stopSound();
    Serial.println("Sound command OFF");
    return;
  }

  digitalWrite(STATUS_LED_PIN, HIGH);

  JsonArrayConst melody = doc["payload"]["melody"].as<JsonArrayConst>();
  if (!melody.isNull() && melody.size() > 0) {
    Serial.printf("Playing melody for node_id=%u\n", NODE_ID);
    playMelody(melody);
  } else {
    uint16_t frequencyHz = doc["payload"]["frequency_hz"] | 1800;
    uint32_t durationMs = doc["payload"]["duration_ms"] | 1200;
    Serial.printf(
      "Playing tone for node_id=%u, freq=%uHz, duration=%lu ms\n",
      NODE_ID,
      frequencyHz,
      static_cast<unsigned long>(durationMs)
    );
    playFrequency(frequencyHz, durationMs);
  }

  digitalWrite(STATUS_LED_PIN, LOW);
}

void playMelody(JsonArrayConst melody) {
  for (JsonObjectConst item : melody) {
    uint32_t durationMs = item["duration_ms"] | 0;
    if (durationMs == 0) {
      continue;
    }

    if (item["tone"].isNull()) {
      stopSound();
      delay(durationMs);
      continue;
    }

    int toneType = item["tone"] | 0;
    if (toneType <= 0) {
      stopSound();
      delay(durationMs);
      continue;
    }

    playNoteType(toneType, durationMs);
  }

  stopSound();
}

void playFrequency(uint16_t frequencyHz, uint32_t durationMs) {
  if (frequencyHz == 0 || durationMs == 0) {
    stopSound();
    return;
  }

  uint32_t startedAt = millis();
  uint32_t halfPeriodMicros = 500000UL / frequencyHz;
  if (halfPeriodMicros == 0) {
    halfPeriodMicros = 1;
  }

  while (millis() - startedAt < durationMs) {
    buzzer.setVoltage(DAC_HIGH, false);
    buzzer.setVoltage(DAC_LOW, false);
    delayMicroseconds(halfPeriodMicros);
    yield();
  }

  stopSound();
}

void playNoteType(int toneType, uint32_t durationMs) {
  uint16_t toneDelay = toneDelayMicrosForType(toneType);
  if (toneDelay == 0 || durationMs == 0) {
    stopSound();
    delay(durationMs);
    return;
  }

  delay(10);
  for (uint32_t index = 0; index < durationMs; index++) {
    buzzer.setVoltage(DAC_HIGH, false);
    buzzer.setVoltage(DAC_LOW, false);
    delayMicroseconds(toneDelay);
    if ((index & 31U) == 0) {
      yield();
    }
  }

  stopSound();
}

uint16_t toneDelayMicrosForType(int toneType) {
  switch (toneType) {
    case 1: return 1000;
    case 2: return 860;
    case 3: return 800;
    case 4: return 700;
    case 5: return 600;
    case 6: return 525;
    case 7: return 450;
    case 8: return 380;
    case 9: return 315;
    case 10: return 250;
    case 11: return 190;
    case 12: return 130;
    case 13: return 80;
    case 14: return 30;
    case 15: return 1;
    default: return 0;
  }
}

void stopSound() {
  buzzer.setVoltage(0, false);
}

void setRangeIndicator(bool detected) {
  digitalWrite(RANGE_LED_PIN_1, detected ? HIGH : LOW);
  digitalWrite(RANGE_LED_PIN_2, detected ? HIGH : LOW);
}
