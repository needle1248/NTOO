from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from digital_guide.core.models import CityConfig
from digital_guide.core.state import AppStateStore
from digital_guide.services.congestion_engine import CongestionEngine
from digital_guide.services.eta_engine import ETAEngine
from digital_guide.services.recommendation_engine import RecommendationEngine


class CityStatePoller:
    def __init__(
        self,
        store: AppStateStore,
        logger: logging.Logger,
        city_config: CityConfig,
        eta_engine: ETAEngine,
        congestion_engine: CongestionEngine,
        recommendation_engine: RecommendationEngine,
    ) -> None:
        self.store = store
        self.logger = logger
        self.city_config = city_config
        self.eta_engine = eta_engine
        self.congestion_engine = congestion_engine
        self.recommendation_engine = recommendation_engine

    async def run(self) -> None:
        if not self.city_config.enabled:
            self.logger.info("city integration disabled, state polling skipped")
            return
        while True:
            try:
                await self.poll_once()
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("city state poll failed", extra={"payload": {"error": str(exc)}})
            await asyncio.sleep(self.city_config.poll_interval_seconds)

    async def poll_once(self) -> None:
        async with httpx.AsyncClient(timeout=self.city_config.timeout_seconds) as client:
            response = await client.get(self.city_config.state_url)
            response.raise_for_status()
            payload = response.json()
        self.ingest_state(payload)

    def ingest_state(self, payload: dict) -> None:
        for item in payload.get("buses", []):
            timestamp = self._parse_timestamp(item["timestamp"])
            bus_state = self.eta_engine.update_bus(
                bus_id=item["bus_id"],
                ring_id=item["ring_id"],
                current_stop=item["current_stop"],
                timestamp=timestamp,
            )
            baseline = bus_state.rolling_lap_average_seconds
            bus_state.congestion = self.congestion_engine.is_congested(
                baseline_eta=baseline,
                current_eta=item.get("eta_baseline"),
                baseline_lap=baseline,
                current_lap=bus_state.lap_time_seconds,
            )
            self.store.buses[bus_state.bus_id] = bus_state
            self.logger.info("bus update ingested", extra={"payload": bus_state.model_dump(mode="json")})
            if bus_state.congestion:
                self.store.last_congestion_warning = self.congestion_engine.warning_message()

        weather = payload.get("weather")
        if weather:
            snapshot = self.recommendation_engine.evaluate(
                temperature_c=weather["temperature_c"],
                humidity_pct=weather["humidity_pct"],
                pressure_hpa=weather["pressure_hpa"],
            )
            self.store.weather = snapshot
            self.store.last_recommendation_text = snapshot.recommendation_text
            self.logger.info("weather update ingested", extra={"payload": snapshot.model_dump(mode="json")})

    @staticmethod
    def _parse_timestamp(value) -> datetime:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
