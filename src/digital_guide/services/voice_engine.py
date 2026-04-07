from __future__ import annotations

import logging
from collections import deque

from digital_guide.core.models import VoiceMessage
from digital_guide.services.city_event_sender import CityEventSender


class VoiceEngine:
    def __init__(self, city_event_sender: CityEventSender, logger: logging.Logger) -> None:
        self.city_event_sender = city_event_sender
        self.logger = logger
        self.recent_messages: deque[str] = deque(maxlen=5)
        self.synonyms = {
            "go": ["go", "move", "proceed"],
            "wait": ["wait", "pause", "stay ready"],
            "bus": ["bus", "vehicle", "transport"],
        }

    def generate_variants(self, text: str) -> list[str]:
        variants = {text[:200]}
        for source, choices in self.synonyms.items():
            if source in text:
                for choice in choices:
                    variants.add(text.replace(source, choice, 1)[:200])
        return list(variants)[:5]

    async def speak(self, text: str, target_user_id: str | None = None, scenario_id: str | None = None) -> VoiceMessage:
        variants = self.generate_variants(text)
        chosen = next((variant for variant in variants if variant not in self.recent_messages), variants[0])
        self.recent_messages.append(chosen)
        message = VoiceMessage(text=chosen, target_user_id=target_user_id, scenario_id=scenario_id, variants=variants)
        await self.city_event_sender.send(message.model_dump(mode="json"))
        self.logger.info("voice message sent", extra={"payload": message.model_dump(mode="json")})
        return message

