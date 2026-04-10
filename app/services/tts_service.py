from __future__ import annotations

import hashlib
import io
import re
import threading
import wave
from pathlib import Path
from typing import Any

try:
    from piper import PiperVoice
    from piper.config import SynthesisConfig
except ModuleNotFoundError as exc:
    PiperVoice = None
    SynthesisConfig = None
    PIPER_IMPORT_ERROR = exc
else:
    PIPER_IMPORT_ERROR = None

from app.config import Settings


PROJECT_DIR = Path(__file__).resolve().parents[2]


def _resolve_project_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return PROJECT_DIR / candidate


class NeuralTtsService:
    def __init__(self, settings: Settings) -> None:
        self.enabled = settings.tts_enabled
        self.model_path = _resolve_project_path(settings.tts_model_path)
        self.config_path = Path(f"{self.model_path}.json")
        self.cache_dir = _resolve_project_path(settings.tts_cache_dir)
        self.voice_name = settings.tts_voice_name
        self.length_scale = settings.tts_length_scale
        self.noise_scale = settings.tts_noise_scale
        self.noise_w_scale = settings.tts_noise_w_scale
        self.volume = settings.tts_volume
        self.sentence_pause_seconds = settings.tts_sentence_pause_seconds

        self._voice: Any | None = None
        self._lock = threading.Lock()

    def readiness_error(self) -> str | None:
        if not self.enabled:
            return "Нейросетевая озвучка отключена в настройках."
        if PIPER_IMPORT_ERROR is not None:
            return "Для текущего Python не установлен пакет piper-tts."
        if not self.model_path.exists():
            return f"Не найдена TTS-модель: {self.model_path}."
        if not self.config_path.exists():
            return f"Не найден конфиг TTS-модели: {self.config_path}."
        return None

    def is_ready(self) -> bool:
        return self.readiness_error() is None

    def synthesize_bytes(self, text: str) -> bytes:
        cleaned_text = self._normalize_text(text)
        if not cleaned_text:
            raise ValueError("Для озвучки нужен непустой текст.")

        self._ensure_ready()

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = self.cache_dir / f"{self._cache_key(cleaned_text)}.wav"
        if cache_path.exists():
            return cache_path.read_bytes()

        with self._lock:
            if cache_path.exists():
                return cache_path.read_bytes()

            voice = self._load_voice()
            wav_bytes = self._render_wav_bytes(voice, cleaned_text)
            cache_path.write_bytes(wav_bytes)
            return wav_bytes

    def _ensure_ready(self) -> None:
        if not self.enabled:
            raise RuntimeError("Нейросетевая озвучка отключена в настройках.")
        if PIPER_IMPORT_ERROR is not None:
            raise ModuleNotFoundError(
                "Для текущего Python не установлен пакет piper-tts."
            ) from PIPER_IMPORT_ERROR
        if not self.model_path.exists():
            raise FileNotFoundError(f"Не найдена TTS-модель: {self.model_path}.")
        if not self.config_path.exists():
            raise FileNotFoundError(f"Не найден конфиг TTS-модели: {self.config_path}.")

    def _load_voice(self) -> Any:
        self._ensure_ready()
        if self._voice is None:
            self._voice = PiperVoice.load(
                self.model_path,
                config_path=self.config_path,
                download_dir=self.cache_dir,
            )
        return self._voice

    def _render_wav_bytes(self, voice: Any, text: str) -> bytes:
        syn_config = SynthesisConfig(
            length_scale=self.length_scale,
            noise_scale=self.noise_scale,
            noise_w_scale=self.noise_w_scale,
            volume=self.volume,
        )
        chunks = list(voice.synthesize(text, syn_config=syn_config))
        if not chunks:
            raise RuntimeError("Нейросеть не вернула аудио.")

        sample_rate = chunks[0].sample_rate
        silence_samples = max(0, int(sample_rate * self.sentence_pause_seconds))
        silence_bytes = b"\x00\x00" * silence_samples

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            for index, chunk in enumerate(chunks):
                wav_file.writeframes(chunk.audio_int16_bytes)
                if index < len(chunks) - 1 and silence_bytes:
                    wav_file.writeframes(silence_bytes)

        return buffer.getvalue()

    def _cache_key(self, text: str) -> str:
        payload = "|".join(
            [
                self.voice_name,
                f"{self.length_scale:.3f}",
                f"{self.noise_scale:.3f}",
                f"{self.noise_w_scale:.3f}",
                f"{self.volume:.3f}",
                f"{self.sentence_pause_seconds:.3f}",
                text,
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _normalize_text(self, text: str) -> str:
        normalized = re.sub(r"\s+", " ", text).strip()
        normalized = normalized.replace("->", "до")
        return normalized
