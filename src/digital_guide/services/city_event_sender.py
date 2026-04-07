from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx

from digital_guide.core.models import CityConfig
from digital_guide.core.state import AppStateStore


class CityEventSender:
    def __init__(self, store: AppStateStore, logger: logging.Logger, city_config: CityConfig) -> None:
        self.store = store
        self.logger = logger
        self.city_config = city_config
        self.failure_streak = 0
        self.next_retry_at: datetime | None = None
        self.offline_logged = False

    async def run(self) -> None:
        if not self.city_config.enabled:
            if not self.offline_logged:
                self.logger.info("city integration disabled, outgoing events will stay local")
                self.offline_logged = True
            return
        while True:
            if not self.store.pending_city_events:
                await asyncio.sleep(0.2)
                continue
            if self.next_retry_at and datetime.now(timezone.utc) < self.next_retry_at:
                await asyncio.sleep(0.2)
                continue
            payload = self.store.pending_city_events.popleft()
            try:
                await self.send(payload)
                self.failure_streak = 0
                self.next_retry_at = None
            except Exception as exc:  # noqa: BLE001
                self.store.pending_city_events.appendleft(payload)
                self.failure_streak += 1
                delay_seconds = min(2 ** min(self.failure_streak, 4), 15)
                self.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
                self.logger.error(
                    "failed to send city event",
                    extra={
                        "payload": {
                            "error": str(exc),
                            "event": payload,
                            "retry_in_seconds": delay_seconds,
                            "failure_streak": self.failure_streak,
                        }
                    },
                )
                await asyncio.sleep(0.5)

    async def send(self, payload: dict) -> None:
        if not self.city_config.enabled:
            self.logger.info("city integration disabled, event not sent", extra={"payload": payload})
            return
        headers = {"X-Access-Token": self.city_config.access_token}
        url = f"{self.city_config.base_url.rstrip('/')}{self.city_config.event_path}"
        async with httpx.AsyncClient(timeout=self.city_config.timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
        self.logger.info("city event sent", extra={"payload": payload})
