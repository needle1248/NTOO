from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from digital_guide.core.models import DeepSeekConfig, VoiceRouteParameters, VoiceRouteParseRequest, VoiceRouteParseResponse

SYSTEM_PROMPT = """Ты — модуль извлечения параметров маршрута из голосовой команды пользователя в системе «Цифровой поводырь».

Задача:
получить текст голосовой команды на русском языке и вернуть только валидный JSON без пояснений.

Извлеки поля:
- user_id: строка
- start_point: число или null
- target_point: число или null
- via_points: массив чисел
- scenario: одно из значений "walk", "bus", "indoor", "mixed" или ""

Правила:
1. Возвращай только JSON.
2. Никакого markdown.
3. Никаких комментариев.
4. Если значение не найдено — используй пустое значение:
   - user_id = ""
   - start_point = null
   - target_point = null
   - via_points = []
   - scenario = ""
5. Преобразуй словесные сценарии:
   - «пешком» => "walk"
   - «автобус», «на автобусе», «с пересадкой» => "bus"
   - «в МФЦ», «внутри здания», «внутри помещения» => "indoor"
   - «смешанный маршрут» => "mixed"
6. Промежуточные точки возвращай в порядке упоминания.
7. Если пользователь говорит «через точки 3, 5 и 7», верни:
   "via_points": [3, 5, 7]
8. Если сказано «из точки 1 в точку 16» или «от 1 до 16»:
   - start_point = 1
   - target_point = 16

Пример входа:
Построй маршрут для user_demo от точки 1 до точки 16 через 5, 8 и 11, сценарий автобусом

Пример выхода:
{"user_id":"user_demo","start_point":1,"target_point":16,"via_points":[5,8,11],"scenario":"bus"}"""


class DeepSeekRouteParser:
    def __init__(self, config: DeepSeekConfig, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger

    def is_enabled(self) -> bool:
        return self.config.enabled and bool(self.config.api_key.strip())

    async def parse_route_command(self, request: VoiceRouteParseRequest) -> VoiceRouteParseResponse:
        transcript = request.transcript.strip()
        if not transcript:
            raise ValueError("Пустой текст голосовой команды.")
        if not self.is_enabled():
            raise RuntimeError("DeepSeek-R1 не настроен. Укажите deepseek.enabled=true и deepseek.api_key.")

        model_payload = await self._call_model(transcript=transcript, current_user_id=request.current_user_id)
        params = self.normalize_model_payload(model_payload, current_user_id=request.current_user_id)
        missing_fields = self.missing_fields(params)
        message = self._build_message(params, missing_fields)
        return VoiceRouteParseResponse(
            transcript=transcript,
            params=params,
            missing_fields=missing_fields,
            message=message,
            model_name=self.config.model,
            raw_model_json=model_payload,
        )

    async def _call_model(self, transcript: str, current_user_id: str) -> dict[str, Any]:
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        user_prompt = (
            f'Текущий user_id в форме: "{current_user_id}".\n'
            f"Голосовая команда пользователя:\n{transcript}\n"
            "Верни только JSON."
        )
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
        data = response.json()
        content = (((data.get("choices") or [{}])[0]).get("message") or {}).get("content", "")
        if not content:
            raise ValueError("DeepSeek-R1 вернул пустой ответ.")
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:  # noqa: PERF203
            raise ValueError("DeepSeek-R1 вернул невалидный JSON.") from exc

    @staticmethod
    def normalize_model_payload(model_payload: dict[str, Any], current_user_id: str = "") -> VoiceRouteParameters:
        raw_user_id = model_payload.get("user_id") or ""
        raw_start = model_payload.get("start_point")
        raw_target = model_payload.get("target_point")
        raw_via = model_payload.get("via_points") or []
        raw_scenario = (model_payload.get("scenario") or "").strip().lower()

        scenario_aliases = {
            "walk": "walk",
            "bus": "bus",
            "indoor": "indoor",
            "mixed": "mixed",
            "transport": "bus",
            "mfc": "indoor",
        }
        scenario = scenario_aliases.get(raw_scenario, "")

        via_points: list[int] = []
        if isinstance(raw_via, list):
            for item in raw_via:
                try:
                    via_points.append(int(item))
                except (TypeError, ValueError):
                    continue

        user_id = str(raw_user_id).strip()
        if not user_id and current_user_id.strip():
            user_id = current_user_id.strip()

        return VoiceRouteParameters(
            user_id=user_id,
            start_point=DeepSeekRouteParser._safe_int(raw_start),
            target_point=DeepSeekRouteParser._safe_int(raw_target),
            via_points=via_points,
            scenario=scenario,
        )

    @staticmethod
    def missing_fields(params: VoiceRouteParameters) -> list[str]:
        missing: list[str] = []
        if not params.user_id:
            missing.append("user_id")
        if params.start_point is None:
            missing.append("start_point")
        if params.target_point is None:
            missing.append("target_point")
        if not params.scenario:
            missing.append("scenario")
        return missing

    @staticmethod
    def _build_message(params: VoiceRouteParameters, missing_fields: list[str]) -> str:
        if params.start_point is not None and params.target_point is not None:
            if missing_fields:
                return "Маршрут частично распознан. Незаполненные параметры оставлены без изменений."
            return "Маршрут успешно распознан."
        return "Не удалось полностью распознать параметры маршрута."

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
