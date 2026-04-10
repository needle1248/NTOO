from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "NTO Smart City Local Server"
    host: str = "0.0.0.0"
    port: int = 2162

    city_server_base_url: str = "http://192.168.31.63:8000"
    city_event_path: str = "/event"
    city_debug_state_path: str = "/debug/state"
    city_access_token: str = "kids8461"
    city_poll_interval_seconds: float = 2.0
    city_request_timeout_seconds: float = 5.0
    enable_city_polling: bool = True
    city_receive_log_path: str = "logs/city-receive-log.txt"
    city_receive_log_entries_limit: int = 20
    city_receive_log_updates_preview_limit: int = 8
    board_offline_after_seconds: float = 15.0

    team_id: int = 1
    team_name: str = "Команда 1"
    team_config_path: str = "config/team.json"
    reference_data_path: str = "config/reference-data.json"
    data_dir: Path = Path("data")
    faces_dir: Path = Path("data/faces")
    models_dir: Path = Path("data/models")
    snapshot_dir: Path = Path("data/snapshots")
    face_backend: str = "sface"
    face_detector_model_path: Path | None = None
    face_embedding_model_path: Path | None = None
    face_match_threshold: float = 0.92
    face_image_size: int = 96
    face_max_snapshots: int = 25
    camera_rotate_180: bool = True

    tts_enabled: bool = True
    tts_model_path: str = "models/piper/ru_RU-irina-medium.onnx"
    tts_cache_dir: str = ".cache/tts"
    tts_voice_name: str = "ru_RU-irina-medium"
    tts_length_scale: float = 1.08
    tts_noise_scale: float = 0.7
    tts_noise_w_scale: float = 0.8
    tts_volume: float = 0.92
    tts_sentence_pause_seconds: float = 0.16
    text_generation_enabled: bool = True
    text_generation_model_id: str = "Qwen/Qwen2.5-0.5B-Instruct"
    text_generation_model_path: str = "models/text/qwen2.5-0.5b-instruct"
    text_generation_cache_dir: str = ".cache/text-generation"
    text_generation_max_new_tokens: int = 80
    text_generation_temperature: float = 0.35
    text_generation_top_p: float = 0.9
    text_generation_repetition_penalty: float = 1.05

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def city_event_url(self) -> str:
        return f"{self.city_server_base_url.rstrip('/')}{self.city_event_path}"

    @property
    def city_debug_state_url(self) -> str:
        return f"{self.city_server_base_url.rstrip('/')}{self.city_debug_state_path}"


def _load_json(path: str) -> dict[str, Any]:
    json_path = Path(path)
    if not json_path.exists():
        return {}
    return json.loads(json_path.read_text(encoding="utf-8"))


def load_reference_data(settings: Settings) -> dict[str, Any]:
    return _load_json(settings.reference_data_path)


def load_team_profile(settings: Settings) -> dict[str, Any]:
    reference = load_reference_data(settings)
    team_config = _load_json(settings.team_config_path)

    team_id = str(team_config.get("team_id", settings.team_id))
    preset = reference.get("signal_presets", {}).get(team_id, {})
    hero_id = reference.get("heroes", {}).get(team_id)

    profile: dict[str, Any] = {
        "team_id": int(team_id),
        "team_name": team_config.get("team_name", settings.team_name),
        "hero_user_id": team_config.get("hero_user_id", hero_id),
        "signal": {
            "type1": preset.get("type1", {}),
            "type2": preset.get("type2", {}),
        },
        "devices": team_config.get("devices", {}),
        "bus": team_config.get("bus", {}),
        "scenario": team_config.get("scenario", {}),
        "notes": team_config.get("notes", []),
    }

    profile["signal"]["type1"].update(team_config.get("signal", {}).get("type1", {}))
    profile["signal"]["type2"].update(team_config.get("signal", {}).get("type2", {}))
    return profile


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
