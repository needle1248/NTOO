# Arduino-скетчи для стенда

В папке лежат несколько прошивок:

- `esp32_cam_face_node/esp32_cam_face_node.ino`
  `ESP32-CAM` подключается по Wi-Fi и отправляет JPEG-кадры только на локальный сервер.

- `device_node_with_id/device_node_with_id.ino`
  отдельная звуковая плата с фиксированным `device_id`, которая по Wi-Fi опрашивает локальный сервер и подаёт звуковой сигнал по его команде.

- `device_node_mcp4725/device_node_mcp4725.ino`
  звуковая плата на `MCP4725`, которая умеет проигрывать мелодии из `payload.melody`.

- `device_node_mcp4725_vl53l0x/device_node_mcp4725_vl53l0x.ino`
  копия `MCP4725`-прошивки со встроенной поддержкой `VL53L0X`: звук остаётся, а датчик дистанции дополнительно зажигает пины `14/15` и отправляет измерения на `POST /events/distance`.

## Архитектура

Схема такая:

1. `ESP32-CAM -> локальный сервер`
2. `локальный сервер -> звуковая плата с нужным id`

`ESP32-CAM` не общается со звуковой платой напрямую.

## Что поменять перед прошивкой

В обоих скетчах:

- `WIFI_SSID`
- `WIFI_PASSWORD`
- `SERVER_BASE_URL`

В `esp32_cam_face_node.ino`:

- `esp32_cam_config_verify.h`
- `esp32_cam_config_enroll.h`
- активный `#include` в `esp32_cam_face_node.ino`

В `device_node_with_id.ino`:

- `DEVICE_ID`
- `BUZZER_PIN`
- `STATUS_LED_PIN`

В `device_node_mcp4725_vl53l0x.ino`:

- `NODE_ID`
- `DISTANCE_DEVICE_ID`
- `I2C_SDA_PIN`
- `I2C_SCL_PIN`
- `RANGE_LED_PIN_1`
- `RANGE_LED_PIN_2`
- `DISTANCE_TRIGGER_MM`

## Запись лица прямо с ESP32-CAM

Для `ESP32-CAM` теперь есть два отдельных файла конфига:

- `esp32_cam_config_verify.h` для обычной верификации точки
- `esp32_cam_config_enroll.h` для записи фото лица

В `esp32_cam_face_node.ino` нужно оставить активным только один `#include`.

В `esp32_cam_face_node.ino` можно включить:

- `ENROLL_MODE = true`
- `ENROLL_USER_ID = "blind_user_01"`

Тогда камера будет отправлять кадры в endpoint:

`POST /devices/esp32-cam/{POINT_ID}/enroll?user_id=blind_user_01&retrain=false`

Сервер сохранит эти JPEG в папку пользователя `data/faces/<user_id>/`.

После накопления кадров можно:

1. либо включить `ENROLL_RETRAIN_AFTER_EACH_FRAME = true`
2. либо один раз вызвать `POST /faces/retrain`

## Как работает звуковая плата

Звуковая плата:

- знает свой `device_id`;
- опрашивает локальный сервер несколько раз в секунду;
- получает JSON вроде:

```json
{
  "device_id": 102,
  "active": true,
  "command_type": "type1_sound",
  "payload": {
    "frequency_hz": 1800,
    "duration_ms": 1200
  },
  "reason": "route:route_123",
  "updated_at": 1775547000.0,
  "expires_at": 1775547001.2
}
```

Если `active=true`, плата включает буззер на заданной частоте и на заданную длительность.

## Что нужно в Arduino IDE

- плата `ESP32 by Espressif`
- библиотека `ArduinoJson`
- библиотека `Adafruit MCP4725`
- библиотека `VL53L0X`

## Где открыть

- [esp32_cam_face_node.ino](D:/Documents/nto/arduino/esp32_cam_face_node/esp32_cam_face_node.ino)
- [device_node_with_id.ino](D:/Documents/nto/arduino/device_node_with_id/device_node_with_id.ino)
- [device_node_mcp4725.ino](D:/Documents/nto/arduino/device_node_mcp4725/device_node_mcp4725.ino)
- [device_node_mcp4725_vl53l0x.ino](D:/Documents/nto/arduino/device_node_mcp4725_vl53l0x/device_node_mcp4725_vl53l0x.ino)

## MCP4725 playback notes

- Use `device_node_mcp4725.ino` when the board should play a melody from `payload.melody`.
- Set `NODE_ID` on the board equal to the route `node_id`.
- Start the ready-made route JSON with `POST /routes/playback`.

## ESP32-CAM verification notes

- Use `esp32_cam_face_node.ino` for verification points with face recognition.
- Set `POINT_ID` equal to the route point `node_id` for that camera position.
- With `ENROLL_MODE = false`, the firmware sends JPEG frames to `POST /devices/esp32-cam/{POINT_ID}/frame`.
- With `ENROLL_MODE = true`, the firmware sends JPEG frames to `POST /devices/esp32-cam/{POINT_ID}/enroll?...`.
- The sketch now retries failed frame uploads `FRAME_SEND_RETRY_COUNT` times and prints the server JSON response to Serial.

## Board status heartbeat

- All three sketches now send periodic heartbeat to `POST /devices/heartbeat`.
- Status page: `http://127.0.0.1:2162/device-status`
- JSON API: `http://127.0.0.1:2162/api/device-status`
- `device_node_mcp4725.ino` reports itself as `type1_sound`.
- `device_node_with_id.ino` reports itself as `type1_sound`.
- `esp32_cam_face_node.ino` reports itself as `esp32_cam` and includes `capture_mode`.
