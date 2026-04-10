from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path
from typing import Any

os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
os.environ.setdefault("USE_TF", "0")

try:
    import torch
    from huggingface_hub import snapshot_download
    from transformers import AutoModelForCausalLM, AutoTokenizer
except ModuleNotFoundError as exc:
    torch = None
    snapshot_download = None
    AutoModelForCausalLM = None
    AutoTokenizer = None
    TEXT_MODEL_IMPORT_ERROR = exc
else:
    TEXT_MODEL_IMPORT_ERROR = None

from app.config import Settings


PROJECT_DIR = Path(__file__).resolve().parents[2]


def _resolve_project_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return PROJECT_DIR / candidate


class TextGenerationService:
    def __init__(self, settings: Settings) -> None:
        self.enabled = settings.text_generation_enabled
        self.model_id = settings.text_generation_model_id
        self.model_path = _resolve_project_path(settings.text_generation_model_path)
        self.cache_dir = _resolve_project_path(settings.text_generation_cache_dir)
        self.max_new_tokens = settings.text_generation_max_new_tokens
        self.temperature = settings.text_generation_temperature
        self.top_p = settings.text_generation_top_p
        self.repetition_penalty = settings.text_generation_repetition_penalty

        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._rewrites_cache: dict[str, str] = {}
        self._lock = threading.Lock()

    def readiness_error(self) -> str | None:
        if not self.enabled:
            return "Локальная текстовая модель отключена в настройках."
        if TEXT_MODEL_IMPORT_ERROR is not None:
            return "Для генерации текста не установлены transformers, torch и huggingface-hub."
        if not (self.model_path / "config.json").exists():
            return f"Локальная текстовая модель ещё не скачана: {self.model_path}."
        return None

    def is_ready(self) -> bool:
        return self.readiness_error() is None

    def rewrite_text(
        self,
        draft_text: str,
        *,
        intent: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        normalized_draft = self._normalize_output(draft_text)
        if not normalized_draft or not self.enabled:
            return normalized_draft
        if TEXT_MODEL_IMPORT_ERROR is not None:
            return normalized_draft

        payload = {
            "intent": intent,
            "draft_text": normalized_draft,
            "context": context or {},
        }
        cache_key = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        cached = self._rewrites_cache.get(cache_key)
        if cached:
            return cached

        try:
            rewritten = self._generate_rewrite(normalized_draft, intent=intent, context=context or {})
        except Exception:
            rewritten = normalized_draft

        self._rewrites_cache[cache_key] = rewritten
        return rewritten

    def ensure_model_downloaded(self) -> Path:
        if (self.model_path / "config.json").exists():
            return self.model_path
        if TEXT_MODEL_IMPORT_ERROR is not None:
            raise ModuleNotFoundError(
                "Для генерации текста не установлены transformers, torch и huggingface-hub."
            ) from TEXT_MODEL_IMPORT_ERROR

        self.model_path.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        snapshot_download(
            repo_id=self.model_id,
            local_dir=str(self.model_path),
            local_dir_use_symlinks=False,
        )
        return self.model_path

    def _load_model(self) -> tuple[Any, Any]:
        with self._lock:
            if self._tokenizer is not None and self._model is not None:
                return self._tokenizer, self._model

            model_dir = self.ensure_model_downloaded()
            self._tokenizer = AutoTokenizer.from_pretrained(model_dir, trust_remote_code=False)
            self._model = AutoModelForCausalLM.from_pretrained(
                model_dir,
                trust_remote_code=False,
                dtype=torch.float32,
            )
            self._model.eval()
            return self._tokenizer, self._model

    def _build_prompt(self, draft_text: str, intent: str, context: dict[str, Any]) -> str:
        context_json = json.dumps(context, ensure_ascii=False, sort_keys=True)
        return (
            "Ты редактор голосовых сообщений для городской навигации.\n"
            "Перепиши черновик на русском так, чтобы он звучал живо, спокойно и по-человечески.\n"
            "Нельзя придумывать новые факты, менять точки, маршрут, талон, препятствия или способ подтверждения.\n"
            "Сделай максимум 2 коротких предложения и не длиннее 180 символов.\n"
            "Ответь только готовым текстом.\n\n"
            f"Тип сообщения: {intent}\n"
            f"Контекст: {context_json}\n"
            f"Черновик: {draft_text}"
        )

    def _generate_rewrite(self, draft_text: str, *, intent: str, context: dict[str, Any]) -> str:
        tokenizer, model = self._load_model()
        prompt = self._build_prompt(draft_text, intent, context)
        messages = [
            {
                "role": "system",
                "content": (
                    "Ты редактор голосовых сообщений. Делаешь текст дружелюбнее, "
                    "но не меняешь факты и отвечаешь только готовой фразой."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        if hasattr(tokenizer, "apply_chat_template"):
            rendered_prompt = tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
        else:
            rendered_prompt = f"{messages[0]['content']}\n\n{messages[1]['content']}\n"

        inputs = tokenizer(rendered_prompt, return_tensors="pt")
        pad_token_id = tokenizer.pad_token_id or tokenizer.eos_token_id
        generation_kwargs: dict[str, Any] = {
            "max_new_tokens": self.max_new_tokens,
            "pad_token_id": pad_token_id,
            "repetition_penalty": self.repetition_penalty,
        }
        if self.temperature > 0:
            generation_kwargs.update(
                {
                    "do_sample": True,
                    "temperature": self.temperature,
                    "top_p": self.top_p,
                }
            )
        else:
            generation_kwargs["do_sample"] = False

        with torch.no_grad():
            output_ids = model.generate(**inputs, **generation_kwargs)

        prompt_length = inputs["input_ids"].shape[1]
        generated_ids = output_ids[0][prompt_length:]
        raw_text = tokenizer.decode(generated_ids, skip_special_tokens=True)
        return self._normalize_output(raw_text) or draft_text

    def _normalize_output(self, text: str) -> str:
        cleaned = re.sub(r"\s+", " ", (text or "").strip())
        cleaned = cleaned.strip("\"' ")
        if len(cleaned) > 180:
            cleaned = f"{cleaned[:177].rstrip()}..."
        return cleaned
