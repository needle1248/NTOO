#include <Arduino.h>
#include <WiFi.h>
#include <ArduinoJson.h>
#include <esp_camera.h>
#include <esp_heap_caps.h>

// =========================
// Пользовательские настройки
// =========================
// Select one config file before flashing.
#include "esp32_cam_config_verify.h"
// #include "esp32_cam_config_enroll.h"

// =========================
// Тонкая настройка изображения
// =========================
// Для сцены с ярким окном сзади полезно:
// - оставить автоэкспозицию включенной,
// - поднять AE level,
// - слегка поднять brightness.
const int CAMERA_BRIGHTNESS = 1;          // -2 .. 2
const int CAMERA_CONTRAST = 0;            // -2 .. 2
const int CAMERA_SATURATION = 0;          // -2 .. 2
const bool CAMERA_AUTO_EXPOSURE = true;   // true = авто, false = ручная экспозиция
const bool CAMERA_AEC2 = true;            // расширенный автоэкспозамер
const int CAMERA_AE_LEVEL = 2;            // -2 .. 2
const int CAMERA_AEC_VALUE = 450;         // 0 .. 1200, работает при CAMERA_AUTO_EXPOSURE=false
const bool CAMERA_AUTO_GAIN = true;       // true = автоусиление, false = ручное
const int CAMERA_AGC_GAIN = 12;           // 0 .. 30, работает при CAMERA_AUTO_GAIN=false
const gainceiling_t CAMERA_GAIN_CEILING = GAINCEILING_32X;
const bool CAMERA_AUTO_WHITEBAL = true;
const bool CAMERA_AWB_GAIN = true;
const bool CAMERA_LENS_CORRECTION = true;
const bool CAMERA_RAW_GAMMA = true;
const bool CAMERA_BLACK_PIXEL_CORRECTION = true;
const bool CAMERA_WHITE_PIXEL_CORRECTION = true;

// =========================
// AI Thinker ESP32-CAM pins
// =========================
#define PWDN_GPIO_NUM 32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 0
#define SIOD_GPIO_NUM 26
#define SIOC_GPIO_NUM 27
#define Y9_GPIO_NUM 35
#define Y8_GPIO_NUM 34
#define Y7_GPIO_NUM 39
#define Y6_GPIO_NUM 36
#define Y5_GPIO_NUM 21
#define Y4_GPIO_NUM 19
#define Y3_GPIO_NUM 18
#define Y2_GPIO_NUM 5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM 23
#define PCLK_GPIO_NUM 22

uint32_t lastFrameSentAt = 0;
uint32_t lastHeartbeatAt = 0;
uint16_t enrollCapturedCount = 0;
uint32_t successfulFrameUploads = 0;
uint32_t failedFrameUploads = 0;
const uint32_t HEARTBEAT_INTERVAL_MS = 5000;
const char *BOARD_TYPE = "esp32_cam";
const char *FIRMWARE_NAME = "esp32_cam_face_node";

void connectToWifi();
bool initCamera();
void configureCameraSensor(sensor_t *sensor);
void captureAndSendFrame();
String buildFrameUrl();
String buildHeartbeatUrl();
void postHeartbeatIfNeeded();
bool postFrameToServer(camera_fb_t *fb, const String &url);
bool sendHeartbeat();
void printServerResponse(const String &responseBody);
bool parseHttpUrl(const String &url, String &host, uint16_t &port, String &path);
String readHttpLine(WiFiClient &client, uint32_t timeoutMs);
bool readHttpHeaders(WiFiClient &client, String &headers, uint32_t timeoutMs);
int extractContentLength(const String &headers);
String readHttpBody(WiFiClient &client, int contentLength, uint32_t timeoutMs);

void setup() {
  Serial.begin(115200);
  Serial.println();
  Serial.println("ESP32-CAM face node starting...");

  if (!initCamera()) {
    Serial.println("Camera init failed. Restarting in 5 seconds...");
    delay(5000);
    ESP.restart();
  }

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  connectToWifi();
  pinMode(CAMERA_FLASH_LED_PIN, OUTPUT);
  digitalWrite(CAMERA_FLASH_LED_PIN, LOW);

  Serial.printf(
    "Capture mode=%s, point_id=%u, frame_interval_ms=%lu\n",
    ENROLL_MODE ? "enroll" : "verify",
    POINT_ID,
    static_cast<unsigned long>(FRAME_INTERVAL_MS)
  );
  lastFrameSentAt = millis();
  lastHeartbeatAt = 0;
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectToWifi();
  }

  if (millis() - lastFrameSentAt >= FRAME_INTERVAL_MS) {
    lastFrameSentAt = millis();
    captureAndSendFrame();
  }

  postHeartbeatIfNeeded();
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

bool initCamera() {
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 10000000;
  config.pixel_format = PIXFORMAT_JPEG;

  bool hasPsram = psramFound();
  Serial.printf(
    "Camera memory check: psram=%s, freeHeap=%u, freePsram=%u, freeDma=%u\n",
    hasPsram ? "YES" : "NO",
    ESP.getFreeHeap(),
    ESP.getFreePsram(),
    heap_caps_get_free_size(MALLOC_CAP_DMA)
  );

#if defined(CAMERA_GRAB_WHEN_EMPTY)
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
#endif

#if defined(CAMERA_FB_IN_PSRAM)
  config.fb_location = hasPsram ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
#endif

  if (hasPsram) {
    config.frame_size = CAMERA_FRAME_SIZE;
    config.jpeg_quality = CAMERA_JPEG_QUALITY;
    config.fb_count = CAMERA_FB_COUNT;
    Serial.println("Trying camera config: configured/PSRAM");
  } else {
#if defined(CAMERA_FB_IN_DRAM)
    config.fb_location = CAMERA_FB_IN_DRAM;
#endif
    config.frame_size = CAMERA_FALLBACK_FRAME_SIZE;
    config.jpeg_quality = CAMERA_FALLBACK_JPEG_QUALITY;
    config.fb_count = 1;
    Serial.println("Trying camera config: fallback/DRAM");
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("esp_camera_init failed: 0x%x\n", err);
    return false;
  }

  sensor_t *sensor = esp_camera_sensor_get();
  if (sensor != nullptr) {
    configureCameraSensor(sensor);
  }

  Serial.println("Camera ready.");
  return true;
}

void configureCameraSensor(sensor_t *sensor) {
  sensor->set_brightness(sensor, CAMERA_BRIGHTNESS);
  sensor->set_contrast(sensor, CAMERA_CONTRAST);
  sensor->set_saturation(sensor, CAMERA_SATURATION);
  sensor->set_whitebal(sensor, CAMERA_AUTO_WHITEBAL ? 1 : 0);
  sensor->set_awb_gain(sensor, CAMERA_AWB_GAIN ? 1 : 0);
  sensor->set_exposure_ctrl(sensor, CAMERA_AUTO_EXPOSURE ? 1 : 0);
  sensor->set_aec2(sensor, CAMERA_AEC2 ? 1 : 0);
  sensor->set_ae_level(sensor, CAMERA_AE_LEVEL);
  sensor->set_gain_ctrl(sensor, CAMERA_AUTO_GAIN ? 1 : 0);
  sensor->set_gainceiling(sensor, CAMERA_GAIN_CEILING);
  sensor->set_lenc(sensor, CAMERA_LENS_CORRECTION ? 1 : 0);
  sensor->set_raw_gma(sensor, CAMERA_RAW_GAMMA ? 1 : 0);
  sensor->set_bpc(sensor, CAMERA_BLACK_PIXEL_CORRECTION ? 1 : 0);
  sensor->set_wpc(sensor, CAMERA_WHITE_PIXEL_CORRECTION ? 1 : 0);

  if (!CAMERA_AUTO_EXPOSURE) {
    sensor->set_aec_value(sensor, CAMERA_AEC_VALUE);
  }

  if (!CAMERA_AUTO_GAIN) {
    sensor->set_agc_gain(sensor, CAMERA_AGC_GAIN);
  }

  Serial.printf(
    "Camera tuning: brightness=%d contrast=%d saturation=%d autoExp=%d aeLevel=%d aecValue=%d autoGain=%d agcGain=%d\n",
    CAMERA_BRIGHTNESS,
    CAMERA_CONTRAST,
    CAMERA_SATURATION,
    CAMERA_AUTO_EXPOSURE ? 1 : 0,
    CAMERA_AE_LEVEL,
    CAMERA_AEC_VALUE,
    CAMERA_AUTO_GAIN ? 1 : 0,
    CAMERA_AGC_GAIN
  );
}

void captureAndSendFrame() {
  if (ENROLL_MODE && enrollCapturedCount >= ENROLL_CAPTURE_LIMIT) {
    static bool notified = false;
    if (!notified) {
      Serial.println("Enroll capture limit reached. Stop sending frames.");
      notified = true;
    }
    return;
  }

  if (CAMERA_USE_FLASH) {
    digitalWrite(CAMERA_FLASH_LED_PIN, HIGH);
    delay(CAMERA_FLASH_PRELIGHT_MS);
  }

  camera_fb_t *fb = esp_camera_fb_get();

  if (CAMERA_USE_FLASH) {
    digitalWrite(CAMERA_FLASH_LED_PIN, LOW);
  }

  if (fb == nullptr) {
    Serial.println("Camera frame capture failed.");
    return;
  }

  String url = buildFrameUrl();
  Serial.printf(
    "Sending frame to local server: %s (%u bytes), freeHeap=%u, freePsram=%u\n",
    url.c_str(),
    fb->len,
    ESP.getFreeHeap(),
    ESP.getFreePsram()
  );

  bool sentOk = postFrameToServer(fb, url);
  esp_camera_fb_return(fb);
  if (sentOk) {
    successfulFrameUploads++;
  } else {
    failedFrameUploads++;
  }
  Serial.printf("Frame cycle complete, freeHeap=%u, freePsram=%u\n", ESP.getFreeHeap(), ESP.getFreePsram());
  delay(50);
}

String buildFrameUrl() {
  String url = String(SERVER_BASE_URL) + "/devices/esp32-cam/" + String(POINT_ID);
  if (ENROLL_MODE) {
    url += "/enroll?user_id=";
    url += ENROLL_USER_ID;
    url += "&retrain=";
    url += ENROLL_RETRAIN_AFTER_EACH_FRAME ? "true" : "false";
    return url;
  }

  url += "/frame";
  return url;
}

String buildHeartbeatUrl() {
  return String(SERVER_BASE_URL) + "/devices/heartbeat";
}

void postHeartbeatIfNeeded() {
  if (millis() - lastHeartbeatAt < HEARTBEAT_INTERVAL_MS) {
    return;
  }
  lastHeartbeatAt = millis();
  sendHeartbeat();
}

bool postFrameToServer(camera_fb_t *fb, const String &url) {
  for (uint8_t attempt = 1; attempt <= FRAME_SEND_RETRY_COUNT; ++attempt) {
    WiFiClient client;
    client.setTimeout(HTTP_TIMEOUT_MS);

    String host;
    String path;
    uint16_t port = 0;
    if (!parseHttpUrl(url, host, port, path)) {
      Serial.println("Unable to parse server URL.");
      return false;
    }

    if (!client.connect(host.c_str(), port)) {
      Serial.printf(
        "TCP connect failed: host=%s port=%u (attempt %u/%u)\n",
        host.c_str(),
        static_cast<unsigned>(port),
        static_cast<unsigned>(attempt),
        static_cast<unsigned>(FRAME_SEND_RETRY_COUNT)
      );
      if (attempt < FRAME_SEND_RETRY_COUNT) {
        delay(FRAME_SEND_RETRY_DELAY_MS);
      }
      continue;
    }

    String requestHead =
      "POST " + path + " HTTP/1.1\r\n"
      "Host: " + host + ":" + String(port) + "\r\n"
      "Content-Type: image/jpeg\r\n"
      "Content-Length: " + String(fb->len) + "\r\n"
      "Connection: close\r\n\r\n";

    if (client.print(requestHead) != requestHead.length()) {
      Serial.printf(
        "Failed to send HTTP headers (attempt %u/%u)\n",
        static_cast<unsigned>(attempt),
        static_cast<unsigned>(FRAME_SEND_RETRY_COUNT)
      );
      client.stop();
      if (attempt < FRAME_SEND_RETRY_COUNT) {
        delay(FRAME_SEND_RETRY_DELAY_MS);
      }
      continue;
    }

    size_t totalWritten = 0;
    const size_t chunkSize = 1024;
    while (totalWritten < fb->len) {
      size_t bytesLeft = fb->len - totalWritten;
      size_t currentChunk = bytesLeft < chunkSize ? bytesLeft : chunkSize;
      size_t written = client.write(fb->buf + totalWritten, currentChunk);
      if (written == 0) {
        Serial.printf(
          "Socket write failed after %u bytes (attempt %u/%u)\n",
          static_cast<unsigned>(totalWritten),
          static_cast<unsigned>(attempt),
          static_cast<unsigned>(FRAME_SEND_RETRY_COUNT)
        );
        break;
      }
      totalWritten += written;
      delay(1);
    }

    if (totalWritten != fb->len) {
      client.stop();
      if (attempt < FRAME_SEND_RETRY_COUNT) {
        delay(FRAME_SEND_RETRY_DELAY_MS);
      }
      continue;
    }

    String statusLine = readHttpLine(client, HTTP_TIMEOUT_MS);
    statusLine.trim();
    int statusCode = -1;
    int firstSpace = statusLine.indexOf(' ');
    if (firstSpace >= 0) {
      int secondSpace = statusLine.indexOf(' ', firstSpace + 1);
      String codeText = secondSpace > firstSpace
        ? statusLine.substring(firstSpace + 1, secondSpace)
        : statusLine.substring(firstSpace + 1);
      statusCode = codeText.toInt();
    }

    Serial.printf(
      "Server response code: %d (attempt %u/%u)\n",
      statusCode,
      static_cast<unsigned>(attempt),
      static_cast<unsigned>(FRAME_SEND_RETRY_COUNT)
    );

    String responseHeaders;
    if (!readHttpHeaders(client, responseHeaders, HTTP_TIMEOUT_MS)) {
      Serial.println("HTTP response headers were not fully received.");
      client.stop();
      if (attempt < FRAME_SEND_RETRY_COUNT) {
        delay(FRAME_SEND_RETRY_DELAY_MS);
      }
      continue;
    }

    int contentLength = extractContentLength(responseHeaders);
    String responseBody = readHttpBody(client, contentLength, HTTP_TIMEOUT_MS);

    client.stop();
    if (statusCode == 200) {
      if (!responseBody.isEmpty()) {
        printServerResponse(responseBody);
      } else {
        Serial.println("Server returned an empty response body.");
      }
      if (ENROLL_MODE) {
        enrollCapturedCount++;
        Serial.printf("Enroll frames saved: %u / %u\n", enrollCapturedCount, ENROLL_CAPTURE_LIMIT);
      }
      return true;
    }

    if (!responseBody.isEmpty()) {
      Serial.printf("Raw response: %s\n", responseBody.c_str());
    }
    if (attempt < FRAME_SEND_RETRY_COUNT) {
      delay(FRAME_SEND_RETRY_DELAY_MS);
    }
  }

  Serial.println("Frame upload failed after all retries.");
  return false;
}

bool sendHeartbeat() {
  String url = buildHeartbeatUrl();
  String host;
  String path;
  uint16_t port = 0;
  if (!parseHttpUrl(url, host, port, path)) {
    Serial.println("Unable to parse heartbeat URL.");
    return false;
  }

  StaticJsonDocument<512> doc;
  doc["board_type"] = BOARD_TYPE;
  doc["device_id"] = POINT_ID;
  doc["firmware"] = FIRMWARE_NAME;
  doc["ip_address"] = WiFi.localIP().toString();
  doc["mac_address"] = WiFi.macAddress();
  doc["wifi_rssi"] = WiFi.RSSI();
  doc["free_heap"] = ESP.getFreeHeap();
  doc["free_psram"] = ESP.getFreePsram();
  doc["uptime_seconds"] = millis() / 1000;
  JsonObject extra = doc["extra"].to<JsonObject>();
  extra["capture_mode"] = ENROLL_MODE ? "enroll" : "verify";
  extra["frame_interval_ms"] = FRAME_INTERVAL_MS;
  extra["successful_frame_uploads"] = successfulFrameUploads;
  extra["failed_frame_uploads"] = failedFrameUploads;
  extra["enroll_captured_count"] = enrollCapturedCount;

  String body;
  serializeJson(doc, body);

  WiFiClient client;
  client.setTimeout(HTTP_TIMEOUT_MS);
  if (!client.connect(host.c_str(), port)) {
    Serial.printf("Heartbeat TCP connect failed: host=%s port=%u\n", host.c_str(), static_cast<unsigned>(port));
    return false;
  }

  String requestHead =
    "POST " + path + " HTTP/1.1\r\n"
    "Host: " + host + ":" + String(port) + "\r\n"
    "Content-Type: application/json\r\n"
    "Content-Length: " + String(body.length()) + "\r\n"
    "Connection: close\r\n\r\n";

  if (client.print(requestHead) != requestHead.length()) {
    Serial.println("Failed to send heartbeat headers.");
    client.stop();
    return false;
  }

  if (client.print(body) != body.length()) {
    Serial.println("Failed to send heartbeat body.");
    client.stop();
    return false;
  }

  String statusLine = readHttpLine(client, HTTP_TIMEOUT_MS);
  statusLine.trim();
  int statusCode = -1;
  int firstSpace = statusLine.indexOf(' ');
  if (firstSpace >= 0) {
    int secondSpace = statusLine.indexOf(' ', firstSpace + 1);
    String codeText = secondSpace > firstSpace
      ? statusLine.substring(firstSpace + 1, secondSpace)
      : statusLine.substring(firstSpace + 1);
    statusCode = codeText.toInt();
  }

  String responseHeaders;
  readHttpHeaders(client, responseHeaders, HTTP_TIMEOUT_MS);
  client.stop();

  if (statusCode == 200) {
    Serial.printf("Heartbeat sent for %s:%u\n", BOARD_TYPE, POINT_ID);
    return true;
  }

  Serial.printf("Heartbeat failed with status %d\n", statusCode);
  return false;
}

bool parseHttpUrl(const String &url, String &host, uint16_t &port, String &path) {
  const String httpPrefix = "http://";
  if (!url.startsWith(httpPrefix)) {
    return false;
  }

  int hostStart = httpPrefix.length();
  int pathStart = url.indexOf('/', hostStart);
  String hostPort = pathStart >= 0 ? url.substring(hostStart, pathStart) : url.substring(hostStart);
  path = pathStart >= 0 ? url.substring(pathStart) : "/";

  int colonIndex = hostPort.indexOf(':');
  if (colonIndex >= 0) {
    host = hostPort.substring(0, colonIndex);
    port = static_cast<uint16_t>(hostPort.substring(colonIndex + 1).toInt());
  } else {
    host = hostPort;
    port = 80;
  }

  return host.length() > 0 && port > 0 && path.length() > 0;
}

String readHttpLine(WiFiClient &client, uint32_t timeoutMs) {
  String line;
  unsigned long lastActivityAt = millis();
  while (millis() - lastActivityAt < timeoutMs) {
    while (client.available()) {
      char ch = static_cast<char>(client.read());
      line += ch;
      lastActivityAt = millis();
      if (ch == '\n') {
        return line;
      }
    }
    if (!client.connected() && !client.available()) {
      break;
    }
    delay(1);
  }
  return line;
}

bool readHttpHeaders(WiFiClient &client, String &headers, uint32_t timeoutMs) {
  headers = "";
  unsigned long lastActivityAt = millis();
  while (millis() - lastActivityAt < timeoutMs) {
    while (client.available()) {
      char ch = static_cast<char>(client.read());
      headers += ch;
      lastActivityAt = millis();
      if (headers.endsWith("\r\n\r\n")) {
        return true;
      }
    }
    if (!client.connected() && !client.available()) {
      break;
    }
    delay(1);
  }
  return headers.endsWith("\r\n\r\n");
}

int extractContentLength(const String &headers) {
  const String headerName = "content-length:";
  String lowerHeaders = headers;
  lowerHeaders.toLowerCase();
  int contentLengthPos = lowerHeaders.indexOf(headerName);
  if (contentLengthPos < 0) {
    return -1;
  }

  int valueStart = contentLengthPos + headerName.length();
  int lineEnd = lowerHeaders.indexOf("\r\n", valueStart);
  String value = lineEnd >= 0
    ? headers.substring(valueStart, lineEnd)
    : headers.substring(valueStart);
  value.trim();
  return value.toInt();
}

String readHttpBody(WiFiClient &client, int contentLength, uint32_t timeoutMs) {
  String body;
  unsigned long lastActivityAt = millis();
  while (millis() - lastActivityAt < timeoutMs) {
    while (client.available()) {
      char ch = static_cast<char>(client.read());
      body += ch;
      lastActivityAt = millis();
      if (contentLength >= 0 && body.length() >= contentLength) {
        return body;
      }
    }
    if (!client.connected() && !client.available()) {
      break;
    }
    delay(1);
  }
  return body;
}

void printServerResponse(const String &responseBody) {
  StaticJsonDocument<1536> doc;
  DeserializationError err = deserializeJson(doc, responseBody);
  if (err) {
    Serial.printf("Failed to parse JSON: %s\n", err.c_str());
    Serial.printf("Raw response: %s\n", responseBody.c_str());
    return;
  }

  if (ENROLL_MODE) {
    bool accepted = doc["accepted"] | false;
    const char *userId = doc["user_id"] | "";
    int savedCount = doc["saved_count"] | 0;
    const char *savedPath = doc["saved_path"] | "";
    Serial.printf(
      "Enroll response: accepted=%d user_id=%s saved_count=%d\n",
      accepted ? 1 : 0,
      userId,
      savedCount
    );
    Serial.printf("Saved path: %s\n", savedPath);
    return;
  }

  JsonObject prediction = doc["prediction"];
  bool matched = prediction["matched"] | false;
  const char *userId = prediction["user_id"] | "";
  float confidence = prediction["confidence"] | 0.0f;

  if (matched) {
    Serial.printf("Face recognized locally on server: user_id=%s confidence=%.3f\n", userId, confidence);
  } else {
    Serial.println("Face not recognized.");
  }

  JsonArrayConst sessions = doc["advanced_sessions"].as<JsonArrayConst>();
  if (!sessions.isNull() && sessions.size() > 0) {
    Serial.print("Advanced route sessions: ");
    serializeJson(sessions, Serial);
    Serial.println();
  }
}
