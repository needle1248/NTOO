from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

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
        self.stop_to_ring = {
            stop: ring_id for ring_id, stops in self.eta_engine.graph.stop_mappings.items() for stop in stops
        }
        self.ring_order = list(self.eta_engine.graph.stop_mappings.keys())
        self.bus_ring_mapping = {str(key): value for key, value in self.city_config.bus_ring_mapping.items()}
        self.stop_index_mapping = {
            str(ring_id): {str(raw_stop): mapped_stop for raw_stop, mapped_stop in mapping.items()}
            for ring_id, mapping in self.city_config.stop_index_mapping.items()
        }
        self._last_payload_shape: str | None = None
        self._poll_failure_streak = 0
        self._relative_timestamp_anchor: datetime | None = None
        self._last_relative_timestamp: float | None = None

    async def run(self) -> None:
        if not self.city_config.enabled:
            self.logger.info("city integration disabled, state polling skipped")
            return
        while True:
            try:
                await self.poll_once()
                if self._poll_failure_streak:
                    self.logger.info(
                        "city state polling recovered",
                        extra={"payload": {"failure_streak": self._poll_failure_streak}},
                    )
                    self._poll_failure_streak = 0
            except Exception as exc:  # noqa: BLE001
                self._poll_failure_streak += 1
                if self._poll_failure_streak == 1 or self._poll_failure_streak % 5 == 0:
                    self.logger.warning(
                        "city state poll failed",
                        extra={"payload": {"error": str(exc), "failure_streak": self._poll_failure_streak}},
                    )
            await asyncio.sleep(self.city_config.poll_interval_seconds)

    async def poll_once(self) -> None:
        async with httpx.AsyncClient(timeout=self.city_config.timeout_seconds) as client:
            response = await client.get(self.city_config.state_url)
            response.raise_for_status()
            payload = response.json()
        self.ingest_state(payload)

    def ingest_state(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            raise ValueError(f"unsupported city state type: {type(payload).__name__}")

        self._log_payload_shape(payload)

        for index, item in enumerate(self._extract_bus_items(payload), start=1):
            try:
                normalized = self._normalize_bus_item(item, index=index)
                if normalized is None:
                    continue
                if self._should_skip_bus_update(normalized):
                    continue

                timestamp = normalized["timestamp"]
                bus_state = self.eta_engine.update_bus(
                    bus_id=normalized["bus_id"],
                    ring_id=normalized["ring_id"],
                    current_stop=normalized["current_stop"],
                    timestamp=timestamp,
                )
                baseline = bus_state.rolling_lap_average_seconds
                bus_state.congestion = self.congestion_engine.is_congested(
                    baseline_eta=baseline,
                    current_eta=normalized.get("eta_baseline"),
                    baseline_lap=baseline,
                    current_lap=bus_state.lap_time_seconds,
                )
                self.store.buses[bus_state.bus_id] = bus_state
                self.logger.info("bus update ingested", extra={"payload": bus_state.model_dump(mode="json")})
                if bus_state.congestion:
                    self.store.last_congestion_warning = self.congestion_engine.warning_message()
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(
                    "city bus item skipped: processing failed",
                    extra={"payload": {"item": item, "error": str(exc)}},
                )

        weather = self._extract_weather_payload(payload)
        if weather:
            snapshot = self.recommendation_engine.evaluate(
                temperature_c=weather["temperature_c"],
                humidity_pct=weather["humidity_pct"],
                pressure_hpa=weather["pressure_hpa"],
            )
            self.store.weather = snapshot
            self.store.last_recommendation_text = snapshot.recommendation_text
            self.logger.info("weather update ingested", extra={"payload": snapshot.model_dump(mode="json")})

    def _extract_bus_items(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw_buses = self._pick(payload, "buses", "bus", "transport", "bus_states", "transport_state")
        if raw_buses is None:
            return []

        if isinstance(raw_buses, list):
            return [item for item in raw_buses if isinstance(item, dict)]

        if not isinstance(raw_buses, dict):
            self.logger.warning(
                "city buses payload has unsupported type",
                extra={"payload": {"payload_type": type(raw_buses).__name__}},
            )
            return []

        if self._looks_like_bus_item(raw_buses):
            return [raw_buses]

        items: list[dict[str, Any]] = []
        for key, value in raw_buses.items():
            if not isinstance(value, dict):
                continue
            if self._looks_like_bus_item(value):
                item = dict(value)
                item.setdefault("bus_id", str(key))
                items.append(item)
                continue
            for nested_key, nested_value in value.items():
                if not isinstance(nested_value, dict) or not self._looks_like_bus_item(nested_value):
                    continue
                item = dict(nested_value)
                item.setdefault("bus_id", str(nested_key))
                if str(key) in self.eta_engine.graph.stop_mappings:
                    item.setdefault("ring_id", str(key))
                items.append(item)
        return items

    def _normalize_bus_item(self, item: dict[str, Any], index: int) -> dict[str, Any] | None:
        raw_current_stop = self._coerce_int(self._pick(item, "current_stop", "stop_id", "stop", "station_id", "location_id"))
        if raw_current_stop is None:
            self.logger.warning("city bus item skipped: no stop id", extra={"payload": {"item": item}})
            return None

        bus_id_value = self._pick(item, "bus_id", "id", "name", "device_id")
        bus_id = str(bus_id_value) if bus_id_value not in (None, "") else f"city-bus-{index}"

        ring_value = self._pick(item, "ring_id", "ring", "route", "line")
        ring_id = str(ring_value) if ring_value is not None else self.bus_ring_mapping.get(bus_id)
        current_stop, ring_id = self._resolve_stop_and_ring(
            raw_current_stop=raw_current_stop,
            ring_id=ring_id,
            bus_id=bus_id,
        )
        if ring_id not in self.eta_engine.graph.stop_mappings:
            self.logger.warning(
                "city bus item skipped: unknown ring",
                extra={"payload": {"item": item, "resolved_ring_id": ring_id, "current_stop": raw_current_stop}},
            )
            return None

        timestamp_value = self._pick(item, "timestamp", "ts", "time", "updated_at", "last_seen")
        timestamp = self._parse_timestamp(timestamp_value) if timestamp_value is not None else datetime.now(timezone.utc)

        return {
            "bus_id": bus_id,
            "ring_id": ring_id,
            "current_stop": current_stop,
            "timestamp": timestamp,
            "eta_baseline": self._coerce_float(self._pick(item, "eta_baseline", "baseline_eta", "eta")),
        }

    def _should_skip_bus_update(self, normalized: dict[str, Any]) -> bool:
        existing = self.store.buses.get(normalized["bus_id"])
        if not existing:
            return False
        same_ring = existing.ring_id == normalized["ring_id"]
        same_stop = existing.current_stop == normalized["current_stop"]
        if not (same_ring and same_stop):
            return False
        return normalized["timestamp"] <= existing.timestamp

    def _resolve_stop_and_ring(self, raw_current_stop: int, ring_id: str | None, bus_id: str) -> tuple[int, str | None]:
        if ring_id in self.eta_engine.graph.stop_mappings:
            mapped_stop = self._map_stop_index_to_node(ring_id, raw_current_stop)
            return mapped_stop if mapped_stop is not None else raw_current_stop, ring_id

        direct_ring = self.stop_to_ring.get(raw_current_stop)
        if direct_ring is not None:
            return raw_current_stop, direct_ring

        candidate_rings = [
            candidate_ring
            for candidate_ring, stops in self.eta_engine.graph.stop_mappings.items()
            if 1 <= raw_current_stop <= len(stops)
        ]

        if not candidate_rings:
            zero_based_candidates = [
                candidate_ring
                for candidate_ring, stops in self.eta_engine.graph.stop_mappings.items()
                if 0 <= raw_current_stop < len(stops)
            ]
            candidate_rings = zero_based_candidates

        if len(candidate_rings) == 1:
            resolved_ring = candidate_rings[0]
            mapped_stop = self._map_stop_index_to_node(resolved_ring, raw_current_stop)
            return mapped_stop if mapped_stop is not None else raw_current_stop, resolved_ring

        numeric_bus_id = self._coerce_int(bus_id)
        if numeric_bus_id is not None and 1 <= numeric_bus_id <= len(self.ring_order):
            resolved_ring = self.ring_order[numeric_bus_id - 1]
            mapped_stop = self._map_stop_index_to_node(resolved_ring, raw_current_stop)
            return mapped_stop if mapped_stop is not None else raw_current_stop, resolved_ring

        primary_ring = self.store.team_config.primary_ring
        if primary_ring in candidate_rings:
            mapped_stop = self._map_stop_index_to_node(primary_ring, raw_current_stop)
            return mapped_stop if mapped_stop is not None else raw_current_stop, primary_ring

        return raw_current_stop, None

    def _map_stop_index_to_node(self, ring_id: str, raw_current_stop: int) -> int | None:
        stops = self.eta_engine.graph.stop_mappings.get(ring_id, [])
        explicit_mapping = self.stop_index_mapping.get(ring_id, {})
        if str(raw_current_stop) in explicit_mapping:
            return explicit_mapping[str(raw_current_stop)]
        if raw_current_stop in stops:
            return raw_current_stop
        if 1 <= raw_current_stop <= len(stops):
            return stops[raw_current_stop - 1]
        if 0 <= raw_current_stop < len(stops):
            return stops[raw_current_stop]
        if stops:
            normalized_index = (raw_current_stop - 1) % len(stops)
            return stops[normalized_index]
        return None

    def _extract_weather_payload(self, payload: dict[str, Any]) -> dict[str, float] | None:
        candidates = [
            self._pick(payload, "weather", "weather_sensor", "environment", "climate"),
            payload.get("sensors"),
            payload.get("mgs_thp80"),
            payload.get("mgs-thp80"),
        ]

        for candidate in candidates:
            normalized = self._normalize_weather_candidate(candidate)
            if normalized is not None:
                return normalized

        for value in payload.values():
            normalized = self._normalize_weather_candidate(value)
            if normalized is not None:
                return normalized
        return None

    def _normalize_weather_candidate(self, candidate: Any) -> dict[str, float] | None:
        if not isinstance(candidate, dict):
            return None

        direct = self._coerce_weather(candidate)
        if direct is not None:
            return direct

        for nested in candidate.values():
            if not isinstance(nested, dict):
                continue
            normalized = self._coerce_weather(nested)
            if normalized is not None:
                return normalized
        return None

    def _coerce_weather(self, payload: dict[str, Any]) -> dict[str, float] | None:
        temperature = self._coerce_float(self._pick(payload, "temperature_c", "temperature", "temp_c", "temp"))
        humidity = self._coerce_float(self._pick(payload, "humidity_pct", "humidity", "humidity_percent"))
        pressure = self._coerce_float(self._pick(payload, "pressure_hpa", "pressure", "pressure_mmhg"))
        if temperature is None or humidity is None or pressure is None:
            return None
        return {
            "temperature_c": temperature,
            "humidity_pct": humidity,
            "pressure_hpa": pressure,
        }

    def _log_payload_shape(self, payload: dict[str, Any]) -> None:
        keys = sorted(str(key) for key in payload.keys())
        shape = ",".join(keys)
        if shape == self._last_payload_shape:
            return
        self._last_payload_shape = shape
        self.logger.info("city state payload received", extra={"payload": {"top_level_keys": keys}})

    @staticmethod
    def _looks_like_bus_item(payload: dict[str, Any]) -> bool:
        return any(key in payload for key in ("current_stop", "stop_id", "stop", "station_id", "location_id"))

    @staticmethod
    def _pick(payload: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in payload:
                return payload[key]
        return None

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _parse_timestamp(self, value) -> datetime:
        if isinstance(value, (int, float)):
            return self._parse_numeric_timestamp(float(value))
        text = str(value).strip()
        if text.isdigit():
            return self._parse_numeric_timestamp(float(text))
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    def _parse_numeric_timestamp(self, value: float) -> datetime:
        if value >= 1_000_000_000:
            return datetime.fromtimestamp(value, tz=timezone.utc)

        now = datetime.now(timezone.utc)
        if self._relative_timestamp_anchor is None:
            self._relative_timestamp_anchor = now - timedelta(seconds=value)
            self._last_relative_timestamp = value
            return self._relative_timestamp_anchor + timedelta(seconds=value)

        if self._last_relative_timestamp is not None and value + 5 < self._last_relative_timestamp:
            self._relative_timestamp_anchor = now - timedelta(seconds=value)
        self._last_relative_timestamp = value
        return self._relative_timestamp_anchor + timedelta(seconds=value)
