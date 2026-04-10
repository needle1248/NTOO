#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>

// Change these before flashing.
const char *WIFI_SSID = "YOUR_WIFI_SSID";
const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char *SERVER_BASE_URL = "http://192.168.1.10:8080";

const uint16_t DEVICE_ID = 301;
const char *BOARD_TYPE = "weather_bme280";
const char *FIRMWARE_NAME = "weather_node_iotik32a";

const uint8_t I2C_SDA_PIN = 21;
const uint8_t I2C_SCL_PIN = 22;
const uint8_t BME280_ADDRESS_PRIMARY = 0x76;
const uint8_t BME280_ADDRESS_SECONDARY = 0x77;

const uint32_t WEATHER_POST_INTERVAL_MS = 30000;
const uint32_t HEARTBEAT_INTERVAL_MS = 5000;

Adafruit_BME280 bme;

bool bmeReady = false;
uint8_t bmeAddress = BME280_ADDRESS_PRIMARY;
uint32_t lastWeatherPostAt = 0;
uint32_t lastHeartbeatAt = 0;
uint32_t lastWifiRetryAt = 0;

void connectToWifi();
bool initBme280();
void readAndSendWeatherIfNeeded();
void postHeartbeatIfNeeded();
bool postWeather(float temperature, float humidity, float pressure);
bool postHeartbeat();
void printWeather(float temperature, float humidity, float pressure);
void printRecommendationFromResponse(const String &payload);
String buildFallbackAdvice(float temperature, float humidity, float pressure);
String buildUrl(const char *path);

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println();
  Serial.println("Iotik 32A weather node starting...");

  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  bmeReady = initBme280();

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  connectToWifi();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED && millis() - lastWifiRetryAt > 3000) {
    lastWifiRetryAt = millis();
    connectToWifi();
  }

  readAndSendWeatherIfNeeded();
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

bool initBme280() {
  Serial.println("Starting BME280...");

  if (bme.begin(BME280_ADDRESS_PRIMARY, &Wire)) {
    bmeAddress = BME280_ADDRESS_PRIMARY;
    Serial.println("BME280 found at 0x76");
    return true;
  }

  if (bme.begin(BME280_ADDRESS_SECONDARY, &Wire)) {
    bmeAddress = BME280_ADDRESS_SECONDARY;
    Serial.println("BME280 found at 0x77");
    return true;
  }

  Serial.println("BME280 not found. Check wiring and address 0x76/0x77.");
  return false;
}

void readAndSendWeatherIfNeeded() {
  if (!bmeReady) {
    return;
  }

  if (lastWeatherPostAt != 0 && millis() - lastWeatherPostAt < WEATHER_POST_INTERVAL_MS) {
    return;
  }
  lastWeatherPostAt = millis();

  float temperature = bme.readTemperature();
  float humidity = bme.readHumidity();
  float pressure = bme.readPressure() / 100.0F;

  if (isnan(temperature) || isnan(humidity) || isnan(pressure)) {
    Serial.println("BME280 read error.");
    return;
  }

  printWeather(temperature, humidity, pressure);

  if (WiFi.status() != WL_CONNECTED) {
    Serial.print("Fallback advice: ");
    Serial.println(buildFallbackAdvice(temperature, humidity, pressure));
    Serial.println("--------------------------");
    return;
  }

  if (!postWeather(temperature, humidity, pressure)) {
    Serial.print("Fallback advice: ");
    Serial.println(buildFallbackAdvice(temperature, humidity, pressure));
  }

  Serial.println("--------------------------");
}

void postHeartbeatIfNeeded() {
  if (WiFi.status() != WL_CONNECTED) {
    return;
  }
  if (millis() - lastHeartbeatAt < HEARTBEAT_INTERVAL_MS) {
    return;
  }
  lastHeartbeatAt = millis();
  postHeartbeat();
}

bool postWeather(float temperature, float humidity, float pressure) {
  HTTPClient http;
  WiFiClient client;

  String url = buildUrl("/api/sensors/environment");
  if (!http.begin(client, url)) {
    Serial.println("Weather HTTP begin failed.");
    return false;
  }

  StaticJsonDocument<256> doc;
  doc["temperature_c"] = temperature;
  doc["humidity_percent"] = humidity;
  doc["pressure_hpa"] = pressure;

  String body;
  serializeJson(doc, body);

  http.addHeader("Content-Type", "application/json");
  int code = http.POST(body);
  String response = http.getString();
  http.end();

  if (code != 200) {
    Serial.printf("Weather POST returned code %d\n", code);
    if (response.length() > 0) {
      Serial.println(response);
    }
    return false;
  }

  Serial.println("Weather sent to local server.");
  printRecommendationFromResponse(response);
  return true;
}

bool postHeartbeat() {
  HTTPClient http;
  WiFiClient client;

  String url = buildUrl("/devices/heartbeat");
  if (!http.begin(client, url)) {
    Serial.println("Heartbeat HTTP begin failed.");
    return false;
  }

  StaticJsonDocument<640> doc;
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
  extra["sensor"] = "BME280";
  extra["bme280_address"] = bmeAddress == BME280_ADDRESS_PRIMARY ? "0x76" : "0x77";
  extra["weather_post_interval_ms"] = WEATHER_POST_INTERVAL_MS;

  String body;
  serializeJson(doc, body);

  http.addHeader("Content-Type", "application/json");
  int code = http.POST(body);
  http.end();

  if (code == 200) {
    Serial.println("Heartbeat sent.");
    return true;
  }

  Serial.printf("Heartbeat POST returned code %d\n", code);
  return false;
}

void printWeather(float temperature, float humidity, float pressure) {
  Serial.print("Temperature: ");
  Serial.print(temperature, 1);
  Serial.println(" C");

  Serial.print("Humidity: ");
  Serial.print(humidity, 1);
  Serial.println(" %");

  Serial.print("Pressure: ");
  Serial.print(pressure, 1);
  Serial.println(" hPa");
}

void printRecommendationFromResponse(const String &payload) {
  DynamicJsonDocument doc(2048);
  DeserializationError err = deserializeJson(doc, payload);
  if (err) {
    Serial.printf("Recommendation JSON parse error: %s\n", err.c_str());
    return;
  }

  const char *text = doc["recommendation"]["text"] | "";
  const char *generatedBy = doc["recommendation"]["generated_by"] | "";

  if (strlen(text) == 0) {
    Serial.println("Server did not return a clothing recommendation.");
    return;
  }

  if (strlen(generatedBy) > 0) {
    Serial.printf("AI advice (%s): ", generatedBy);
  } else {
    Serial.print("Advice: ");
  }
  Serial.println(text);
}

String buildFallbackAdvice(float temperature, float humidity, float pressure) {
  String advice;

  if (temperature >= 30.0F) {
    advice = "Очень жарко: легкая одежда, кепка и вода.";
  } else if (temperature >= 24.0F) {
    advice = "Тепло: футболки или легкой рубашки должно хватить.";
  } else if (temperature >= 16.0F) {
    advice = "Комфортно: возьми легкую куртку или худи.";
  } else if (temperature >= 5.0F) {
    advice = "Прохладно: лучше куртка, закрытая обувь и длинные штаны.";
  } else {
    advice = "Холодно: пригодятся теплая куртка, шапка и перчатки.";
  }

  if (humidity >= 80.0F) {
    advice += " Влажно: возьми зонт или непромокаемый слой.";
  } else if (humidity <= 30.0F) {
    advice += " Воздух сухой: можно одеться чуть теплее.";
  }

  if (pressure < 990.0F) {
    advice += " Давление низкое: не спеши, если клонит в сон.";
  }

  return advice;
}

String buildUrl(const char *path) {
  return String(SERVER_BASE_URL) + path;
}
