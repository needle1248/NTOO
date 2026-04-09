from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import Settings


class CityClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=settings.city_request_timeout_seconds)

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Access-Token": self.settings.city_access_token,
        }

    async def send_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        started_at = time.perf_counter()
        try:
            response = await self.client.post(
                self.settings.city_event_url,
                json=payload,
                headers=self._headers,
            )
            latency_ms = round((time.perf_counter() - started_at) * 1000, 1)
            return {
                "ok": response.is_success,
                "status_code": response.status_code,
                "response_text": response.text[:1000],
                "latency_ms": latency_ms,
            }
        except httpx.HTTPError as exc:
            latency_ms = round((time.perf_counter() - started_at) * 1000, 1)
            return {
                "ok": False,
                "status_code": None,
                "response_text": str(exc),
                "latency_ms": latency_ms,
            }

    async def fetch_debug_state(self) -> dict[str, Any]:
        response = await self.client.get(self.settings.city_debug_state_url)
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self.client.aclose()

