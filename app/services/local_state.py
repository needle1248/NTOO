from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from app.models import (
    DistanceReading,
    EnvironmentReading,
    FaceEvent,
    LightEvent,
    ObstacleEvent,
    RfidEvent,
    SoundEvent,
    VoiceEvent,
)
from app.services.bus_tracker import BusTracker, extract_bus_records
from app.services.recommendations import (
    build_clothing_recommendation,
    build_obstacle_recommendation,
    build_traffic_recommendation,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalState:
    def __init__(self, team_profile: dict[str, Any]) -> None:
        self.team_profile = team_profile
        self._lock = asyncio.Lock()
        self.started_at = _now_iso()

        self.events_log: deque[dict[str, Any]] = deque(maxlen=200)
        self.environment: dict[str, Any] | None = None
        self.recommendations: dict[str, Any] = {
            "clothing": None,
            "traffic": None,
            "obstacle": None,
        }
        self.distance_state: dict[str, Any] | None = None
        self.vibration_state: dict[str, Any] = {
            "device_id": team_profile.get("devices", {}).get("vibration_device_id"),
            "active": False,
            "reason": None,
            "last_changed_at": None,
        }

        self.city: dict[str, Any] = {
            "connected": False,
            "last_poll_at": None,
            "last_error": None,
            "last_forward_result": None,
            "raw_state": None,
        }
        self.metrics = {"forwarded_ok": 0, "forwarded_failed": 0}

        self.devices_type1: dict[str, dict[str, Any]] = {}
        self.devices_type2: dict[str, dict[str, Any]] = {}
        self.voice_queue: list[dict[str, Any]] = []
        self.obstacles: list[dict[str, Any]] = []
        self.events_rfid: list[dict[str, Any]] = []
        self.events_face: list[dict[str, Any]] = []

        route_by_bus = team_profile.get("bus", {}).get("route_by_bus", {})
        self.target_stop_id = team_profile.get("bus", {}).get("target_stop_id")
        self.bus_tracker = BusTracker(route_by_bus=route_by_bus)

    async def mark_city_error(self, error: str) -> None:
        async with self._lock:
            self.city["connected"] = False
            self.city["last_error"] = error
            self.city["last_poll_at"] = _now_iso()

    async def refresh_city_state(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            self.city["connected"] = True
            self.city["last_error"] = None
            self.city["last_poll_at"] = _now_iso()
            self.city["raw_state"] = payload

            self.devices_type1 = self._normalize_device_bucket(payload.get("devices_type1"))
            self.devices_type2 = self._normalize_device_bucket(payload.get("devices_type2"))
            self.voice_queue = self._normalize_records(payload.get("voice_queue"))
            self.obstacles = self._normalize_records(payload.get("obstacles"))

            events = payload.get("events", {}) if isinstance(payload.get("events"), dict) else {}
            self.events_rfid = self._normalize_records(events.get("rfid"))
            self.events_face = self._normalize_records(events.get("face"))

            self.bus_tracker.record_many(extract_bus_records(payload))
            self._rebuild_recommendations()

    async def register_forwarded_event(
        self,
        event: VoiceEvent | SoundEvent | LightEvent | RfidEvent | ObstacleEvent | FaceEvent,
        city_result: dict[str, Any],
    ) -> None:
        async with self._lock:
            self.city["last_forward_result"] = city_result
            if city_result["ok"]:
                self.metrics["forwarded_ok"] += 1
            else:
                self.metrics["forwarded_failed"] += 1

            self._append_log(
                {
                    "category": "event",
                    "timestamp": _now_iso(),
                    "payload": event.model_dump(),
                    "city_result": city_result,
                }
            )
            self._apply_event_locally(event)
            self._rebuild_recommendations()

    async def update_environment(self, reading: EnvironmentReading) -> None:
        async with self._lock:
            self.environment = {
                **reading.model_dump(),
                "timestamp": reading.timestamp or int(time.time()),
            }
            self._append_log(
                {
                    "category": "environment",
                    "timestamp": _now_iso(),
                    "payload": self.environment,
                }
            )
            self._rebuild_recommendations()

    async def update_distance(self, reading: DistanceReading) -> None:
        async with self._lock:
            triggered = bool(reading.bus_detected) or reading.distance_cm <= reading.threshold_cm
            self.distance_state = {
                **reading.model_dump(),
                "timestamp": reading.timestamp or int(time.time()),
            }
            self.vibration_state = {
                "device_id": self.team_profile.get("devices", {}).get("vibration_device_id")
                or reading.device_id,
                "active": triggered,
                "reason": "bus_detected" if triggered else "clear",
                "last_changed_at": _now_iso(),
            }
            self._append_log(
                {
                    "category": "distance",
                    "timestamp": _now_iso(),
                    "payload": self.distance_state,
                    "vibration": self.vibration_state,
                }
            )

    async def get_device_command(self, device_id: int, device_type: str) -> dict[str, Any]:
        async with self._lock:
            if device_type == "type1":
                command = self.devices_type1.get(str(device_id), {})
                return {
                    "device_id": device_id,
                    "device_type": device_type,
                    "active": bool(command),
                    "command": command,
                }
            if device_type == "type2":
                command = self.devices_type2.get(str(device_id), {})
                return {
                    "device_id": device_id,
                    "device_type": device_type,
                    "active": bool(command),
                    "command": command,
                }
            return {
                "device_id": device_id,
                "device_type": device_type,
                "active": self.vibration_state["active"],
                "command": self.vibration_state,
            }

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            buses = self.bus_tracker.snapshot(self.target_stop_id)
            return {
                "started_at": self.started_at,
                "team": self.team_profile,
                "city": {
                    "connected": self.city["connected"],
                    "last_poll_at": self.city["last_poll_at"],
                    "last_error": self.city["last_error"],
                    "last_forward_result": self.city["last_forward_result"],
                },
                "metrics": self.metrics,
                "devices_type1": self.devices_type1,
                "devices_type2": self.devices_type2,
                "voice_queue": self.voice_queue,
                "obstacles": self.obstacles,
                "events": {
                    "rfid": self.events_rfid,
                    "face": self.events_face,
                },
                "environment": self.environment,
                "distance": self.distance_state,
                "vibration": self.vibration_state,
                "recommendations": self.recommendations,
                "buses": buses,
                "logs": list(self.events_log),
            }

    async def raw_city_state(self) -> dict[str, Any] | None:
        async with self._lock:
            return self.city["raw_state"]

    def _normalize_device_bucket(self, payload: Any) -> dict[str, dict[str, Any]]:
        if isinstance(payload, dict):
            result = {}
            for device_id, state in payload.items():
                if isinstance(state, dict):
                    result[str(device_id)] = state
                else:
                    result[str(device_id)] = {"value": state}
            return result

        if isinstance(payload, list):
            result = {}
            for item in payload:
                if not isinstance(item, dict):
                    continue
                device_id = item.get("device_id") or item.get("id")
                if device_id is None:
                    continue
                result[str(device_id)] = item
            return result

        return {}

    def _normalize_records(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            normalized = []
            for item in payload:
                if isinstance(item, dict):
                    normalized.append(item)
                else:
                    normalized.append({"value": item})
            return normalized[:20]
        return []

    def _apply_event_locally(
        self,
        event: VoiceEvent | SoundEvent | LightEvent | RfidEvent | ObstacleEvent | FaceEvent,
    ) -> None:
        dumped = event.model_dump()
        if isinstance(event, VoiceEvent):
            self.voice_queue.insert(0, {"text": event.text, "timestamp": dumped.get("timestamp")})
            self.voice_queue = self.voice_queue[:20]
            return
        if isinstance(event, SoundEvent):
            self.devices_type1[str(event.device_id)] = dumped
            return
        if isinstance(event, LightEvent):
            self.devices_type2[str(event.device_id)] = dumped
            return
        if isinstance(event, RfidEvent):
            self.events_rfid.insert(0, dumped)
            self.events_rfid = self.events_rfid[:20]
            return
        if isinstance(event, ObstacleEvent):
            self.obstacles.insert(0, dumped)
            self.obstacles = self.obstacles[:20]
            return
        if isinstance(event, FaceEvent):
            self.events_face.insert(0, dumped)
            self.events_face = self.events_face[:20]

    def _rebuild_recommendations(self) -> None:
        buses = self.bus_tracker.snapshot(self.target_stop_id)
        self.recommendations["clothing"] = build_clothing_recommendation(self.environment)
        self.recommendations["traffic"] = build_traffic_recommendation(
            buses.get("best_eta_seconds"),
            buses.get("baseline_eta_seconds"),
        )
        self.recommendations["obstacle"] = build_obstacle_recommendation(self.obstacles)

    def _append_log(self, entry: dict[str, Any]) -> None:
        self.events_log.appendleft(entry)
