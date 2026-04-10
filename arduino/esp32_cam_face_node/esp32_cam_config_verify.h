#pragma once

const char *WIFI_SSID = "arsIk";
const char *WIFI_PASSWORD = "admin54421";
const char *SERVER_BASE_URL = "http://10.40.145.95:2162";

// Must match the verification point id used in the route payload.
const uint16_t POINT_ID = 1;
const uint32_t FRAME_INTERVAL_MS = 800;

const bool ENROLL_MODE = false;
const char *ENROLL_USER_ID = "blind_user_01";
const bool ENROLL_RETRAIN_AFTER_EACH_FRAME = false;
const uint16_t ENROLL_CAPTURE_LIMIT = 10;

const uint32_t HTTP_TIMEOUT_MS = 5000;
const uint8_t FRAME_SEND_RETRY_COUNT = 2;
const uint16_t FRAME_SEND_RETRY_DELAY_MS = 300;

const framesize_t CAMERA_FRAME_SIZE = FRAMESIZE_QVGA;
const int CAMERA_JPEG_QUALITY = 14;
const uint8_t CAMERA_FB_COUNT = 1;
const framesize_t CAMERA_FALLBACK_FRAME_SIZE = FRAMESIZE_QQVGA;
const int CAMERA_FALLBACK_JPEG_QUALITY = 20;

const bool CAMERA_USE_FLASH = true;
const int CAMERA_FLASH_LED_PIN = 4;
const uint16_t CAMERA_FLASH_PRELIGHT_MS = 90;
