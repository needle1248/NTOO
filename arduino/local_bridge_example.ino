#include <HTTPClient.h>
#include <WiFi.h>

const char* WIFI_SSID = "NTO_MGBOT_CITY";
const char* WIFI_PASS = "Terminator812";

// Укажите IP ноутбука/ПК, на котором поднят этот локальный сервер.
const char* LOCAL_SERVER = "http://192.168.31.100:8080";

const int TYPE1_DEVICE_ID = 1;

void postJson(const String& path, const String& json) {
  if (WiFi.status() != WL_CONNECTED) {
    return;
  }

  HTTPClient http;
  http.begin(String(LOCAL_SERVER) + path);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(json);
  String response = http.getString();
  Serial.printf("POST %s -> %d\n%s\n", path.c_str(), code, response.c_str());
  http.end();
}

void getCommand() {
  if (WiFi.status() != WL_CONNECTED) {
    return;
  }

  HTTPClient http;
  http.begin(
    String(LOCAL_SERVER) +
    "/api/devices/" +
    String(TYPE1_DEVICE_ID) +
    "/command?device_type=type1"
  );
  int code = http.GET();
  String response = http.getString();
  Serial.printf("GET command -> %d\n%s\n", code, response.c_str());
  http.end();
}

void setup() {
  Serial.begin(115200);

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) {
    delay(300);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected");
}

void loop() {
  postJson(
    "/api/events",
    "{\"type\":4,\"device_id\":1,\"rfid_code\":\"ABC123XYZ\"}"
  );

  postJson(
    "/api/sensors/distance",
    "{\"device_id\":99,\"distance_cm\":28.5,\"threshold_cm\":40,\"bus_detected\":true}"
  );

  getCommand();
  delay(5000);
}
