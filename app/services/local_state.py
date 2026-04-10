from __future__ import annotations

import asyncio
import json
import re
import time
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
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


def _now_unix() -> int:
    return int(time.time())


def _extract_point_number(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None

    match = re.search(r"(\d+)$", value)
    if not match:
        return None
    return int(match.group(1))


ForwardedEvent = VoiceEvent | SoundEvent | LightEvent | RfidEvent | ObstacleEvent | FaceEvent
NavigationFollowUpEvent = VoiceEvent | SoundEvent | LightEvent

CONNECTION_TYPE_LABELS = {
    "walk": "Пешая связь",
    "bus": "Автобусная связь",
    "crosswalk": "Пешеходный переход",
}

CONNECTION_TYPE_SPOKEN = {
    "walk": "пешком",
    "bus": "автобусом",
    "crosswalk": "через пешеходный переход",
}


class LocalState:
    def __init__(
        self,
        team_profile: dict[str, Any],
        signal_catalog: dict[str, Any] | None = None,
        city_receive_log_path: str | None = None,
        city_receive_log_entries_limit: int = 20,
        city_receive_log_updates_preview_limit: int = 8,
        text_generation_service: Any | None = None,
    ) -> None:
        self.team_profile = team_profile
        self.signal_catalog = signal_catalog or {}
        self.text_generation_service = text_generation_service
        self._lock = asyncio.Lock()
        self.started_at = _now_iso()
        self.city_receive_log_path = Path(city_receive_log_path) if city_receive_log_path else None
        self.city_receive_log_entries: deque[dict[str, Any]] = deque(
            maxlen=max(city_receive_log_entries_limit, 1)
        )
        self.city_receive_log_updates_preview_limit = max(city_receive_log_updates_preview_limit, 1)

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
            "collector": {
                "snapshots_seen": 0,
                "updates_seen": 0,
                "last_update_at": None,
                "last_snapshot_summary": None,
            },
        }
        self.city_updates: deque[dict[str, Any]] = deque(maxlen=400)
        self.city_snapshots: deque[dict[str, Any]] = deque(maxlen=25)
        self._city_list_item_signatures: dict[str, set[str]] = {}
        self._city_value_signatures: dict[str, str] = {}
        self._city_container_members: dict[str, set[str]] = {}
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
        self._rewrite_city_receive_log()

        scenario = team_profile.get("scenario", {})
        self.navigation_points = self._build_navigation_points(scenario)
        self.navigation_points_by_id = {
            point["point_id"]: point for point in self.navigation_points
        }
        self.navigation_graph = self._build_navigation_graph(scenario)
        self.navigation_services = self._build_navigation_services(scenario)
        self._service_ticket_counter = 0
        self.navigation_state = self._build_initial_navigation_state(scenario)

    async def mark_city_error(self, error: str) -> None:
        observed_at = _now_iso()
        async with self._lock:
            self.city["connected"] = False
            self.city["last_error"] = error
            self.city["last_poll_at"] = observed_at
            self._append_city_receive_log_entry(
                {
                    "observed_at": observed_at,
                    "kind": "error",
                    "error": error,
                }
            )
            self._rewrite_city_receive_log()

    async def refresh_city_state(self, payload: dict[str, Any]) -> None:
        observed_at = _now_iso()
        snapshot_payload = deepcopy(payload)
        async with self._lock:
            self.city["connected"] = True
            self.city["last_error"] = None
            self.city["last_poll_at"] = observed_at
            self.city["raw_state"] = snapshot_payload

            snapshot_summary = self._store_city_snapshot(snapshot_payload, observed_at)
            updates_count = self._collect_city_updates(snapshot_payload, observed_at)
            self.city["collector"]["snapshots_seen"] += 1
            self.city["collector"]["updates_seen"] += updates_count
            self.city["collector"]["last_snapshot_summary"] = snapshot_summary
            if updates_count:
                self.city["collector"]["last_update_at"] = observed_at

            self.devices_type1 = self._normalize_device_bucket(snapshot_payload.get("devices_type1"))
            self.devices_type2 = self._normalize_device_bucket(snapshot_payload.get("devices_type2"))
            self.voice_queue = self._normalize_records(snapshot_payload.get("voice_queue"))
            self.obstacles = self._normalize_records(snapshot_payload.get("obstacles"))

            events = (
                snapshot_payload.get("events", {})
                if isinstance(snapshot_payload.get("events"), dict)
                else {}
            )
            self.events_rfid = self._normalize_records(events.get("rfid"))
            self.events_face = self._normalize_records(events.get("face"))

            self.bus_tracker.record_many(extract_bus_records(snapshot_payload))
            self._rebuild_recommendations()
            self._append_city_receive_log_entry(
                {
                    "observed_at": observed_at,
                    "kind": "snapshot",
                    "summary": deepcopy(snapshot_summary),
                    "updates_count": updates_count,
                    "update_preview": deepcopy(
                        list(self.city_updates)[
                            : min(self.city_receive_log_updates_preview_limit, updates_count)
                        ]
                    ),
                }
            )
            self._rewrite_city_receive_log()

    async def start_navigation(
        self,
        destination_point_id: str | None = None,
        start_point_id: str | None = None,
        waypoint_point_ids: list[str] | None = None,
        service_id: str | None = None,
    ) -> list[NavigationFollowUpEvent]:
        async with self._lock:
            if not self.navigation_points:
                raise ValueError("В config/team.json не настроены навигационные точки схемы.")

            resolved_waypoint_ids = self._normalize_waypoint_ids(waypoint_point_ids or [])
            service = None
            ticket_number = None
            resolved_destination_id = destination_point_id
            if service_id:
                service = self.navigation_services.get(service_id)
                if service is None:
                    raise ValueError("Выбранная услуга не настроена в config/team.json.")
                resolved_destination_id = service["destination_point_id"]
                if start_point_id is None and service.get("start_point_id") in self.navigation_points_by_id:
                    start_point_id = str(service["start_point_id"])
                ticket_number = self._next_ticket_number(str(service.get("queue_prefix") or "A"))

            if not resolved_destination_id:
                raise ValueError("Нужно указать точку назначения или услугу.")

            resolved_start_id = start_point_id or self.navigation_state["start_point_id"]
            if resolved_start_id not in self.navigation_points_by_id:
                raise ValueError("Стартовая точка маршрута не найдена.")
            if resolved_destination_id not in self.navigation_points_by_id:
                raise ValueError("Точка назначения не найдена.")
            resolved_waypoint_ids = [
                point_id
                for point_id in resolved_waypoint_ids
                if point_id not in {resolved_start_id, resolved_destination_id}
            ]

            start_point = self.navigation_points_by_id[resolved_start_id]
            destination_point = self.navigation_points_by_id[resolved_destination_id]
            route_segments = self._build_navigation_route(
                resolved_start_id,
                resolved_destination_id,
                waypoint_point_ids=resolved_waypoint_ids,
                blocked_point_ids=self._blocked_point_ids(),
            )

            self.navigation_state.update(
                {
                    "active": bool(route_segments),
                    "status": "awaiting_confirmation" if route_segments else "completed",
                    "message": None,
                    "start_point_id": resolved_start_id,
                    "destination_point_id": resolved_destination_id,
                    "route_segments": route_segments,
                    "route_point_ids": [segment["to_point_id"] for segment in route_segments],
                    "current_route_index": 0 if route_segments else None,
                    "confirmed_point_ids": [],
                    "waypoint_point_ids": resolved_waypoint_ids,
                    "service_id": service_id,
                    "service_name": service["service_name"] if service else None,
                    "ticket_number": ticket_number,
                    "started_at": _now_iso(),
                    "completed_at": None if route_segments else _now_iso(),
                    "last_confirmation": None,
                }
            )

            if not route_segments:
                base_message = self._compose_voice_message(
                    self._service_intro(service, ticket_number),
                    (
                        f"Вы уже находитесь у {self._spoken_point_name(destination_point)}, "
                        "поэтому строить маршрут не нужно."
                    ),
                )
                message = self._refine_voice_text(
                    base_message,
                    intent="navigation_already_there",
                    context={
                        "start_point_id": resolved_start_id,
                        "destination_point_id": resolved_destination_id,
                    },
                )
                self.navigation_state["message"] = message
                self.navigation_state["start_point_id"] = resolved_destination_id
                self._append_navigation_log(
                    "start",
                    {
                        "start_point_id": resolved_start_id,
                        "destination_point_id": resolved_destination_id,
                        "waypoint_point_ids": resolved_waypoint_ids,
                        "service_id": service_id,
                        "route": [],
                        "message": message,
                    },
                )
                return [self._voice_event(message)]

            first_segment = route_segments[0]
            first_point = self.navigation_points_by_id[first_segment["to_point_id"]]
            route_text = self._format_route_sequence(route_segments)
            base_message = self._compose_voice_message(
                self._service_intro(service, ticket_number),
                f"Маршрут до {self._spoken_point_name(destination_point)} готов.",
                f"Стартуем от {start_point['name']}.",
                self._build_waypoint_notice(resolved_waypoint_ids),
                self._build_step_prompt(first_segment, lead="Сначала"),
            )
            message = self._refine_voice_text(
                base_message,
                intent="navigation_start",
                context={
                    "start_point_id": resolved_start_id,
                    "destination_point_id": resolved_destination_id,
                    "waypoint_point_ids": resolved_waypoint_ids,
                    "route_text": route_text,
                    "service_id": service_id,
                    "ticket_number": ticket_number,
                },
            )
            self.navigation_state["message"] = message
            self._append_navigation_log(
                "start",
                {
                    "start_point_id": resolved_start_id,
                    "destination_point_id": resolved_destination_id,
                    "waypoint_point_ids": resolved_waypoint_ids,
                    "service_id": service_id,
                    "ticket_number": ticket_number,
                    "route": [segment["to_point_id"] for segment in route_segments],
                    "first_point_id": first_point["point_id"],
                },
            )
            return self._build_navigation_events(message, first_point)

    async def register_forwarded_event(
        self,
        event: ForwardedEvent,
        city_result: dict[str, Any],
    ) -> list[NavigationFollowUpEvent]:
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
            follow_ups = self._advance_navigation(event)
            if isinstance(event, ObstacleEvent):
                follow_ups.extend(self._reroute_navigation_for_obstacle(event))
            self._rebuild_recommendations()
            return follow_ups

    async def update_environment(self, reading: EnvironmentReading) -> None:
        async with self._lock:
            self.environment = {
                **reading.model_dump(),
                "timestamp": reading.timestamp or _now_unix(),
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
                "timestamp": reading.timestamp or _now_unix(),
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
                    "collector": self._collector_snapshot(),
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
                "navigation": self._navigation_snapshot(),
                "logs": list(self.events_log),
                "city_updates_preview": deepcopy(list(self.city_updates)[:15]),
            }

    async def raw_city_state(self, snapshot_limit: int = 5) -> dict[str, Any]:
        async with self._lock:
            return {
                "collector": self._collector_snapshot(),
                "raw": deepcopy(self.city["raw_state"]),
                "snapshots": [
                    {
                        "observed_at": entry["observed_at"],
                        "summary": deepcopy(entry["summary"]),
                        "payload": deepcopy(entry["payload"]),
                    }
                    for entry in list(self.city_snapshots)[:snapshot_limit]
                ],
            }

    async def city_feed(self, limit: int = 50) -> dict[str, Any]:
        async with self._lock:
            return {
                "collector": self._collector_snapshot(),
                "updates": deepcopy(list(self.city_updates)[:limit]),
            }

    def _collector_snapshot(self) -> dict[str, Any]:
        collector = self.city["collector"]
        return {
            "snapshots_seen": collector["snapshots_seen"],
            "updates_seen": collector["updates_seen"],
            "snapshots_buffered": len(self.city_snapshots),
            "updates_buffered": len(self.city_updates),
            "receive_log_entries": len(self.city_receive_log_entries),
            "receive_log_path": str(self.city_receive_log_path) if self.city_receive_log_path else None,
            "last_update_at": collector["last_update_at"],
            "last_snapshot_summary": deepcopy(collector["last_snapshot_summary"]),
        }

    def _store_city_snapshot(self, payload: dict[str, Any], observed_at: str) -> dict[str, Any]:
        summary = self._build_city_snapshot_summary(payload)
        self.city_snapshots.appendleft(
            {
                "observed_at": observed_at,
                "summary": summary,
                "payload": payload,
            }
        )
        return summary

    def _build_city_snapshot_summary(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {"root_type": type(payload).__name__}

        collection_sizes = {}
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                collection_sizes[str(key)] = len(value)

        return {
            "root_keys": sorted(str(key) for key in payload.keys()),
            "collection_sizes": collection_sizes,
            "bus_records": len(extract_bus_records(payload)),
        }

    def _collect_city_updates(self, payload: Any, observed_at: str) -> int:
        updates_count = 0

        def walk(path: str, value: Any) -> None:
            nonlocal updates_count

            container_key = path or "__root__"
            if isinstance(value, list):
                seen_signatures = self._city_list_item_signatures.setdefault(container_key, set())
                for item in value:
                    signature = self._fingerprint(item)
                    if signature in seen_signatures:
                        continue
                    seen_signatures.add(signature)
                    self._record_city_update(
                        path=path,
                        kind="list_item",
                        value=item,
                        observed_at=observed_at,
                    )
                    updates_count += 1
                return

            if isinstance(value, dict):
                is_container = (
                    not path
                    or self._is_collection_container(value)
                    or container_key in self._city_container_members
                )
                if path and not is_container:
                    if self._record_city_value_change(path, value, observed_at):
                        updates_count += 1
                    return

                current_children = set()
                for key, nested in value.items():
                    key_str = str(key)
                    current_children.add(key_str)
                    child_path = f"{path}.{key_str}" if path else key_str
                    if isinstance(nested, (dict, list)):
                        walk(child_path, nested)
                    elif self._record_city_value_change(child_path, nested, observed_at):
                        updates_count += 1

                previous_children = self._city_container_members.get(container_key, set())
                for removed_key in sorted(previous_children - current_children):
                    removed_path = f"{path}.{removed_key}" if path else removed_key
                    self._city_value_signatures.pop(removed_path, None)
                    self._record_city_update(
                        path=removed_path,
                        kind="removed",
                        value=None,
                        observed_at=observed_at,
                    )
                    updates_count += 1
                self._city_container_members[container_key] = current_children
                return

            if path and self._record_city_value_change(path, value, observed_at):
                updates_count += 1

        walk("", payload)
        return updates_count

    def _record_city_value_change(self, path: str, value: Any, observed_at: str) -> bool:
        signature = self._fingerprint(value)
        if self._city_value_signatures.get(path) == signature:
            return False

        self._city_value_signatures[path] = signature
        self._record_city_update(
            path=path,
            kind="state_change",
            value=value,
            observed_at=observed_at,
        )
        return True

    def _record_city_update(self, path: str, kind: str, value: Any, observed_at: str) -> None:
        self.city_updates.appendleft(
            {
                "observed_at": observed_at,
                "path": path,
                "kind": kind,
                "value": deepcopy(value),
            }
        )

    def _is_collection_container(self, value: dict[str, Any]) -> bool:
        if not value:
            return False

        values = list(value.values())
        if any(isinstance(item, list) for item in values):
            return True
        return all(isinstance(item, dict) for item in values)

    def _fingerprint(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    def _append_city_receive_log_entry(self, entry: dict[str, Any]) -> None:
        self.city_receive_log_entries.appendleft(entry)

    def _rewrite_city_receive_log(self) -> None:
        if self.city_receive_log_path is None:
            return

        self.city_receive_log_path.parent.mkdir(parents=True, exist_ok=True)
        content = self._render_city_receive_log()
        self.city_receive_log_path.write_text(content, encoding="utf-8")

    def _render_city_receive_log(self) -> str:
        if not self.city_receive_log_entries:
            return "No city receptions yet.\n"

        blocks = [self._format_city_receive_log_entry(entry) for entry in self.city_receive_log_entries]
        return "\n\n".join(blocks) + "\n"

    def _format_city_receive_log_entry(self, entry: dict[str, Any]) -> str:
        lines = [
            f"time: {entry['observed_at']}",
            f"kind: {entry['kind']}",
        ]

        if entry["kind"] == "error":
            lines.append(f"error: {entry['error']}")
            return "\n".join(lines)

        summary = entry.get("summary", {})
        lines.append(f"bus_records: {summary.get('bus_records', 0)}")
        lines.append(
            "collection_sizes: "
            + json.dumps(summary.get("collection_sizes", {}), ensure_ascii=False, sort_keys=True)
        )
        lines.append(f"updates_count: {entry.get('updates_count', 0)}")

        for update in entry.get("update_preview", []):
            update_value = json.dumps(update.get("value"), ensure_ascii=False, sort_keys=True)
            lines.append(
                "update: "
                f"{update.get('path') or '/'} [{update.get('kind', 'update')}] {update_value}"
            )

        return "\n".join(lines)

    def _build_navigation_points(self, scenario: Any) -> list[dict[str, Any]]:
        scenario_data = scenario if isinstance(scenario, dict) else {}
        configured_points = scenario_data.get("points")
        point_configs = configured_points if isinstance(configured_points, list) else []
        default_signal = self.team_profile.get("signal", {})

        points: list[dict[str, Any]] = []
        for index, point_config in enumerate(point_configs):
            if not isinstance(point_config, dict):
                continue
            point = self._make_navigation_point(index, point_config, default_signal)
            if point is not None:
                points.append(point)

        if points:
            return points

        for index, device_id in enumerate(self.team_profile.get("devices", {}).get("type1_ids") or []):
            point = self._make_navigation_point(
                index,
                {"device_id": device_id},
                default_signal,
            )
            if point is not None:
                points.append(point)
        return points

    def _make_navigation_point(
        self,
        index: int,
        point_config: dict[str, Any],
        default_signal: dict[str, Any],
    ) -> dict[str, Any] | None:
        point_id = str(point_config.get("point_id") or point_config.get("id") or f"point-{index + 1}")
        point_number = _extract_point_number(point_id) or index + 1

        raw_device_id = point_config.get("device_id")
        device_type = str(point_config.get("device_type") or "type1").lower()
        resolved_device_id = (
            int(raw_device_id)
            if raw_device_id is not None
            else point_number
        )

        preset_signal = self.signal_catalog.get(str(resolved_device_id), {}).get("type1") or {}
        signal_override = point_config.get("signal", {})
        confirmation = point_config.get("confirmation", {})
        confirmation = confirmation.copy() if isinstance(confirmation, dict) else {}
        type1_signal = default_signal.get("type1", {}) if isinstance(default_signal, dict) else {}
        type2_signal = default_signal.get("type2", {}) if isinstance(default_signal, dict) else {}

        if point_config.get("rfid_device_id") is not None:
            confirmation["rfid_device_id"] = point_config["rfid_device_id"]
        if point_config.get("face_device_id") is not None:
            confirmation["face_device_id"] = point_config["face_device_id"]

        frequency_hz = int(
            point_config.get("frequency_hz")
            or signal_override.get("frequency_hz")
            or preset_signal.get("frequency_hz")
            or (point_number * 100)
            or type1_signal.get("frequency_hz")
            or 1000
        )
        duration_ms = int(
            point_config.get("duration_ms")
            or signal_override.get("duration_ms")
            or preset_signal.get("duration_ms")
            or type1_signal.get("duration_ms")
            or 1000
        )
        color = point_config.get("color") or signal_override.get("color") or type2_signal.get("color")

        return {
            "point_id": point_id,
            "name": str(point_config.get("name") or point_config.get("label") or f"Точка {point_number}"),
            "device_id": resolved_device_id,
            "device_type": device_type,
            "frequency_hz": frequency_hz,
            "duration_ms": duration_ms,
            "color": color,
            "ring_index": index,
            "confirmation": confirmation,
        }

    def _build_navigation_services(self, scenario: Any) -> dict[str, dict[str, Any]]:
        scenario_data = scenario if isinstance(scenario, dict) else {}
        configured_services = scenario_data.get("services")
        service_configs = configured_services if isinstance(configured_services, list) else []

        services: dict[str, dict[str, Any]] = {}
        for service_config in service_configs:
            if not isinstance(service_config, dict):
                continue
            service_id = str(service_config.get("service_id") or "").strip()
            destination_point_id = str(service_config.get("destination_point_id") or "").strip()
            if not service_id or destination_point_id not in self.navigation_points_by_id:
                continue

            services[service_id] = {
                "service_id": service_id,
                "service_name": str(service_config.get("service_name") or service_id),
                "destination_point_id": destination_point_id,
                "start_point_id": service_config.get("start_point_id"),
                "queue_prefix": str(service_config.get("queue_prefix") or "A"),
            }
        return services

    def _build_navigation_graph(self, scenario: Any) -> dict[str, list[dict[str, str]]]:
        graph: dict[str, list[dict[str, str]]] = {
            point_id: [] for point_id in self.navigation_points_by_id
        }

        scenario_data = scenario if isinstance(scenario, dict) else {}
        edge_configs = scenario_data.get("edges")
        if not isinstance(edge_configs, list):
            return graph

        for edge in edge_configs:
            if not isinstance(edge, dict):
                continue
            left = str(edge.get("from") or "")
            right = str(edge.get("to") or "")
            connection_type = str(edge.get("connection_type") or edge.get("type") or "walk")
            self._add_graph_edge(graph, left, right, connection_type)

        return graph

    def _add_graph_edge(
        self,
        graph: dict[str, list[dict[str, str]]],
        left: str,
        right: str,
        connection_type: str,
    ) -> None:
        if left not in graph or right not in graph or left == right:
            return

        left_edge = {"to_point_id": right, "connection_type": connection_type}
        right_edge = {"to_point_id": left, "connection_type": connection_type}

        if left_edge not in graph[left]:
            graph[left].append(left_edge)
        if right_edge not in graph[right]:
            graph[right].append(right_edge)

    def _build_initial_navigation_state(self, scenario: Any) -> dict[str, Any]:
        scenario_data = scenario if isinstance(scenario, dict) else {}
        configured_start_id = scenario_data.get("start_point_id")
        if configured_start_id in self.navigation_points_by_id:
            start_point_id = configured_start_id
        elif self.navigation_points:
            start_point_id = self.navigation_points[0]["point_id"]
        else:
            start_point_id = None

        enabled = bool(self.navigation_points)
        return {
            "enabled": enabled,
            "active": False,
            "status": "idle" if enabled else "not_configured",
            "message": "Маршрут не запущен." if enabled else "Навигационные точки не настроены.",
            "start_point_id": start_point_id,
            "destination_point_id": None,
            "route_segments": [],
            "route_point_ids": [],
            "current_route_index": None,
            "confirmed_point_ids": [],
            "waypoint_point_ids": [],
            "service_id": None,
            "service_name": None,
            "ticket_number": None,
            "started_at": None,
            "completed_at": None,
            "last_confirmation": None,
        }

    def _build_navigation_route(
        self,
        start_point_id: str,
        destination_point_id: str,
        waypoint_point_ids: list[str] | None = None,
        blocked_point_ids: list[str] | None = None,
    ) -> list[dict[str, str]]:
        if start_point_id == destination_point_id and not waypoint_point_ids:
            return []

        segments: list[dict[str, str]] = []
        current_start_id = start_point_id
        target_point_ids = list(waypoint_point_ids or [])
        target_point_ids.append(destination_point_id)
        for target_point_id in target_point_ids:
            if target_point_id == current_start_id:
                continue
            segments.extend(
                self._build_graph_route(
                    current_start_id,
                    target_point_id,
                    blocked_point_ids=blocked_point_ids,
                )
            )
            current_start_id = target_point_id
        return segments

    def _collapse_route_segments(
        self,
        segments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        collapsed: list[dict[str, Any]] = []
        for segment in segments:
            normalized_segment = {
                "from_point_id": segment["from_point_id"],
                "to_point_id": segment["to_point_id"],
                "connection_type": segment["connection_type"],
            }
            via_point_ids = list(segment.get("via_point_ids") or [])
            if via_point_ids:
                normalized_segment["via_point_ids"] = via_point_ids

            if (
                collapsed
                and normalized_segment["connection_type"] == "bus"
                and collapsed[-1]["connection_type"] == "bus"
                and collapsed[-1]["to_point_id"] == normalized_segment["from_point_id"]
            ):
                merged_via_point_ids = list(collapsed[-1].get("via_point_ids") or [])
                merged_via_point_ids.append(collapsed[-1]["to_point_id"])
                merged_via_point_ids.extend(via_point_ids)
                collapsed[-1]["to_point_id"] = normalized_segment["to_point_id"]
                if merged_via_point_ids:
                    collapsed[-1]["via_point_ids"] = merged_via_point_ids
                continue

            collapsed.append(normalized_segment)

        return collapsed

    def _build_graph_route(
        self,
        start_point_id: str,
        destination_point_id: str,
        blocked_point_ids: list[str] | None = None,
    ) -> list[dict[str, str]]:
        if not any(self.navigation_graph.values()):
            raise ValueError("Не удалось построить маршрут: граф связей пуст.")
        blocked_points = set(blocked_point_ids or [])
        if destination_point_id in blocked_points and destination_point_id != start_point_id:
            raise ValueError("Точка назначения недоступна из-за препятствия.")

        parents: dict[str, dict[str, str | None]] = {
            start_point_id: {"from_point_id": None, "connection_type": None}
        }
        queue: deque[str] = deque([start_point_id])

        while queue:
            current_point_id = queue.popleft()
            for edge in self.navigation_graph.get(current_point_id, []):
                neighbor_id = edge["to_point_id"]
                if neighbor_id in blocked_points and neighbor_id != destination_point_id:
                    continue
                if neighbor_id in parents:
                    continue

                parents[neighbor_id] = {
                    "from_point_id": current_point_id,
                    "connection_type": edge["connection_type"],
                }

                if neighbor_id == destination_point_id:
                    segments: list[dict[str, str]] = []
                    cursor = destination_point_id
                    while cursor != start_point_id:
                        parent = parents[cursor]
                        from_point_id = parent["from_point_id"]
                        if from_point_id is None:
                            break
                        segments.append(
                            {
                                "from_point_id": from_point_id,
                                "to_point_id": cursor,
                                "connection_type": str(parent["connection_type"] or "walk"),
                            }
                        )
                        cursor = from_point_id
                    segments.reverse()
                    return segments

                queue.append(neighbor_id)

        raise ValueError("Не удалось построить маршрут по схеме между выбранными точками.")

    def _normalize_point_id(self, value: Any) -> str | None:
        if value is None:
            return None
        if str(value) in self.navigation_points_by_id:
            return str(value)

        point_number = _extract_point_number(value)
        if point_number is None:
            return None

        candidate = f"point-{point_number}"
        if candidate in self.navigation_points_by_id:
            return candidate
        return None

    def _normalize_waypoint_ids(self, waypoint_point_ids: list[str]) -> list[str]:
        normalized: list[str] = []
        for point_id in waypoint_point_ids:
            resolved_point_id = self._normalize_point_id(point_id)
            if resolved_point_id is None:
                raise ValueError(f"Промежуточная точка {point_id!r} не найдена.")
            if resolved_point_id not in normalized:
                normalized.append(resolved_point_id)
        return normalized

    def _blocked_point_ids(self) -> list[str]:
        blocked: list[str] = []
        for obstacle in self.obstacles:
            if not obstacle.get("reroute_required"):
                continue
            point_id = self._normalize_point_id(obstacle.get("location_id"))
            if point_id is not None and point_id not in blocked:
                blocked.append(point_id)
        return blocked

    def _remaining_waypoint_ids(self, current_start_id: str) -> list[str]:
        confirmed_point_ids = set(self.navigation_state["confirmed_point_ids"])
        confirmed_point_ids.add(current_start_id)
        return [
            point_id
            for point_id in self.navigation_state["waypoint_point_ids"]
            if point_id not in confirmed_point_ids
        ]

    def _advance_navigation(self, event: ForwardedEvent) -> list[NavigationFollowUpEvent]:
        if not self.navigation_state["active"]:
            return []
        if not isinstance(event, (RfidEvent, FaceEvent)):
            return []

        current_point = self._current_navigation_point()
        current_segment = self._current_navigation_segment()
        if current_point is None or current_segment is None:
            return []
        if not self._is_matching_confirmation(current_point, event):
            return []

        source = "RFID" if isinstance(event, RfidEvent) else "распознавание лица"
        self.navigation_state["confirmed_point_ids"].append(current_point["point_id"])
        self.navigation_state["last_confirmation"] = {
            "type": "rfid" if isinstance(event, RfidEvent) else "face",
            "device_id": event.device_id,
            "timestamp": _now_iso(),
            "point_id": current_point["point_id"],
            "point_name": current_point["name"],
        }
        self.navigation_state["start_point_id"] = current_point["point_id"]

        current_index = self.navigation_state["current_route_index"]
        route_segments = self.navigation_state["route_segments"]
        if current_index is None or current_index >= len(route_segments) - 1:
            self.navigation_state["active"] = False
            self.navigation_state["status"] = "completed"
            self.navigation_state["current_route_index"] = None
            self.navigation_state["completed_at"] = _now_iso()
            base_message = self._compose_voice_message(
                f"{current_point['name']} подтверждена через {source}.",
                f"Маршрут завершён, вы на месте: {self._spoken_point_name(current_point)}.",
            )
            message = self._refine_voice_text(
                base_message,
                intent="navigation_completed",
                context={
                    "point_id": current_point["point_id"],
                    "source": source,
                },
            )
            self.navigation_state["message"] = message
            self._append_navigation_log(
                "completed",
                {
                    "point_id": current_point["point_id"],
                    "source": source,
                },
            )
            return [self._voice_event(message)]

        next_index = current_index + 1
        next_segment = route_segments[next_index]
        next_point = self.navigation_points_by_id[next_segment["to_point_id"]]
        self.navigation_state["current_route_index"] = next_index
        self.navigation_state["status"] = "awaiting_confirmation"
        self.navigation_state["message"] = self._refine_voice_text(
            self._compose_voice_message(
                f"{current_point['name']} подтверждена через {source}.",
                self._build_step_prompt(next_segment, lead="Дальше"),
            ),
            intent="navigation_advance",
            context={
                "confirmed_point_id": current_point["point_id"],
                "next_point_id": next_point["point_id"],
                "source": source,
            },
        )
        self._append_navigation_log(
            "advance",
            {
                "confirmed_point_id": current_point["point_id"],
                "next_point_id": next_point["point_id"],
                "source": source,
            },
        )
        return self._build_navigation_events(self.navigation_state["message"], next_point)

    def _reroute_navigation_for_obstacle(self, event: ObstacleEvent) -> list[NavigationFollowUpEvent]:
        if not event.reroute_required or not self.navigation_state["active"]:
            return []

        blocked_point_id = self._normalize_point_id(event.location_id)
        if blocked_point_id is None:
            return []

        current_index = self.navigation_state["current_route_index"]
        route_segments = self.navigation_state["route_segments"]
        remaining_segments = route_segments[current_index:] if current_index is not None else route_segments
        remaining_point_ids = {
            point_id
            for segment in remaining_segments
            for point_id in self._segment_point_ids(segment)
        }
        if blocked_point_id not in remaining_point_ids:
            return []

        current_start_id = self.navigation_state["start_point_id"]
        destination_point_id = self.navigation_state["destination_point_id"]
        if current_start_id is None or destination_point_id is None:
            return []

        blocked_point_ids = self._blocked_point_ids()
        remaining_waypoint_ids = [
            point_id
            for point_id in self._remaining_waypoint_ids(current_start_id)
            if point_id not in blocked_point_ids
        ]

        try:
            rerouted_segments = self._build_navigation_route(
                current_start_id,
                destination_point_id,
                waypoint_point_ids=remaining_waypoint_ids,
                blocked_point_ids=blocked_point_ids,
            )
        except ValueError:
            message = self._refine_voice_text(
                self._compose_voice_message(
                    f"На пути обнаружено препятствие у {self._point_name_or_id(blocked_point_id)}.",
                    "Сейчас не удалось подобрать безопасный обходной маршрут.",
                ),
                intent="navigation_reroute_failed",
                context={
                    "blocked_point_id": blocked_point_id,
                    "location_id": event.location_id,
                },
            )
            self.navigation_state["active"] = False
            self.navigation_state["status"] = "blocked"
            self.navigation_state["current_route_index"] = None
            self.navigation_state["message"] = message
            self._append_navigation_log(
                "reroute_failed",
                {
                    "location_id": event.location_id,
                    "blocked_point_id": blocked_point_id,
                },
            )
            return [self._voice_event(message)]

        if rerouted_segments == remaining_segments:
            return []

        self.navigation_state["route_segments"] = rerouted_segments
        self.navigation_state["route_point_ids"] = [
            segment["to_point_id"] for segment in rerouted_segments
        ]
        self.navigation_state["current_route_index"] = 0 if rerouted_segments else None
        self.navigation_state["status"] = "awaiting_confirmation" if rerouted_segments else "completed"
        self.navigation_state["active"] = bool(rerouted_segments)

        if not rerouted_segments:
            message = self._refine_voice_text(
                "Маршрут после перестроения уже завершён.",
                intent="navigation_reroute_completed",
                context={
                    "blocked_point_id": blocked_point_id,
                    "location_id": event.location_id,
                },
            )
            self.navigation_state["message"] = message
            self.navigation_state["completed_at"] = _now_iso()
            self._append_navigation_log(
                "reroute_completed",
                {
                    "location_id": event.location_id,
                    "blocked_point_id": blocked_point_id,
                },
            )
            return [self._voice_event(message)]

        next_point = self.navigation_points_by_id[rerouted_segments[0]["to_point_id"]]
        message = self._refine_voice_text(
            self._compose_voice_message(
                f"На пути обнаружено препятствие у {self._point_name_or_id(blocked_point_id)}.",
                f"Я перестроил маршрут. Новый путь: {self._format_route_sequence(rerouted_segments)}.",
                self._build_step_prompt(rerouted_segments[0], lead="Теперь"),
            ),
            intent="navigation_reroute",
            context={
                "blocked_point_id": blocked_point_id,
                "location_id": event.location_id,
                "route": [segment["to_point_id"] for segment in rerouted_segments],
                "next_point_id": next_point["point_id"],
            },
        )
        self.navigation_state["message"] = message
        self._append_navigation_log(
            "reroute",
            {
                "location_id": event.location_id,
                "blocked_point_id": blocked_point_id,
                "next_point_id": next_point["point_id"],
                "route": [segment["to_point_id"] for segment in rerouted_segments],
            },
        )
        return self._build_navigation_events(message, next_point)

    def _current_navigation_segment(self) -> dict[str, str] | None:
        current_index = self.navigation_state["current_route_index"]
        route_segments = self.navigation_state["route_segments"]
        if current_index is None:
            return None
        if current_index < 0 or current_index >= len(route_segments):
            return None
        return route_segments[current_index]

    def _current_navigation_point(self) -> dict[str, Any] | None:
        current_segment = self._current_navigation_segment()
        if current_segment is None:
            return None
        return self.navigation_points_by_id.get(current_segment["to_point_id"])

    def _segment_point_ids(self, segment: dict[str, Any]) -> list[str]:
        return [*list(segment.get("via_point_ids") or []), segment["to_point_id"]]

    def _is_matching_confirmation(
        self,
        point: dict[str, Any],
        event: RfidEvent | FaceEvent,
    ) -> bool:
        confirmation = point.get("confirmation", {})
        allowed_methods = []
        if confirmation.get("rfid_device_id") is not None:
            allowed_methods.append("rfid")
        if confirmation.get("face_device_id") is not None:
            allowed_methods.append("face")

        if isinstance(event, RfidEvent):
            if not allowed_methods:
                return True
            if "rfid" not in allowed_methods:
                return False
            expected = confirmation.get("rfid_device_id")
            return expected is None or int(expected) == event.device_id

        if not allowed_methods:
            return True
        if "face" not in allowed_methods:
            return False
        expected = confirmation.get("face_device_id")
        return expected is None or int(expected) == event.device_id

    def _build_navigation_events(
        self,
        message: str,
        point: dict[str, Any],
    ) -> list[NavigationFollowUpEvent]:
        events: list[NavigationFollowUpEvent] = [self._voice_event(message)]
        if point.get("device_id") is not None:
            if point.get("device_type") == "type2" and point.get("color"):
                events.append(
                    LightEvent(
                        type=3,
                        device_id=int(point["device_id"]),
                        color=point["color"],
                    )
                )
            else:
                events.append(
                    SoundEvent(
                        type=2,
                        device_id=int(point["device_id"]),
                        frequency_hz=int(point["frequency_hz"]),
                        duration_ms=int(point["duration_ms"]),
                    )
                )
        return events

    def _voice_event(self, text: str) -> VoiceEvent:
        normalized = text if len(text) <= 200 else f"{text[:197].rstrip()}..."
        return VoiceEvent(type=1, text=normalized, timestamp=_now_unix())

    def _refine_voice_text(
        self,
        draft_text: str,
        *,
        intent: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        service = self.text_generation_service
        if service is None:
            return draft_text
        try:
            return service.rewrite_text(draft_text, intent=intent, context=context or {})
        except Exception:
            return draft_text

    def _compose_voice_message(self, *parts: str | None) -> str:
        return " ".join(part.strip() for part in parts if part and part.strip())

    def _service_intro(
        self,
        service: dict[str, Any] | None,
        ticket_number: str | None,
    ) -> str:
        if not service or not ticket_number:
            return ""
        return f"Услуга «{service['service_name']}», талон {ticket_number}."

    def _build_waypoint_notice(self, waypoint_point_ids: list[str]) -> str:
        if not waypoint_point_ids:
            return ""
        return f"По пути заглянем в {self._format_point_list(waypoint_point_ids)}."

    def _format_point_list(self, point_ids: list[str]) -> str:
        point_names = [
            self.navigation_points_by_id[point_id]["name"]
            for point_id in point_ids
            if point_id in self.navigation_points_by_id
        ]
        if not point_names:
            return ""
        if len(point_names) == 1:
            return point_names[0]
        if len(point_names) == 2:
            return f"{point_names[0]} и {point_names[1]}"
        return f"{', '.join(point_names[:-1])} и {point_names[-1]}"

    def _point_name_or_id(self, point_id: str) -> str:
        point = self.navigation_points_by_id.get(point_id)
        return point["name"] if point else point_id

    def _navigation_snapshot(self) -> dict[str, Any]:
        current_segment = self._current_navigation_segment()
        current_point = self._current_navigation_point()
        confirmed_point_ids = set(self.navigation_state["confirmed_point_ids"])
        route_segments = self.navigation_state["route_segments"]
        display_route_segments = self._collapse_route_segments(route_segments)

        route: list[dict[str, Any]] = []
        for segment in display_route_segments:
            segment_point_ids = self._segment_point_ids(segment)
            status = "pending"
            if segment_point_ids and all(
                point_id in confirmed_point_ids for point_id in segment_point_ids
            ):
                status = "confirmed"
            elif current_segment and current_segment["to_point_id"] in segment_point_ids:
                status = "active"

            route.append(
                {
                    **segment,
                    "from_point": self._serialize_navigation_point(
                        self.navigation_points_by_id.get(segment["from_point_id"])
                    ),
                    "to_point": self._serialize_navigation_point(
                        self.navigation_points_by_id.get(segment["to_point_id"])
                    ),
                    "via_point_ids": list(segment.get("via_point_ids") or []),
                    "connection_type_label": self._connection_type_label(segment["connection_type"]),
                    "instruction": self._format_route_segment(segment),
                    "status": status,
                }
            )

        return {
            "enabled": self.navigation_state["enabled"],
            "active": self.navigation_state["active"],
            "status": self.navigation_state["status"],
            "message": self.navigation_state["message"],
            "started_at": self.navigation_state["started_at"],
            "completed_at": self.navigation_state["completed_at"],
            "start_point": self._serialize_navigation_point(
                self.navigation_points_by_id.get(self.navigation_state["start_point_id"])
            ),
            "destination": self._serialize_navigation_point(
                self.navigation_points_by_id.get(self.navigation_state["destination_point_id"])
            ),
            "current_point": self._serialize_navigation_point(current_point),
            "current_step": (
                {
                    **current_segment,
                    "from_point": self._serialize_navigation_point(
                        self.navigation_points_by_id.get(current_segment["from_point_id"])
                    ),
                    "to_point": self._serialize_navigation_point(current_point),
                    "via_point_ids": list(current_segment.get("via_point_ids") or []),
                    "connection_type_label": self._connection_type_label(
                        current_segment["connection_type"]
                    ),
                    "instruction": self._format_route_segment(current_segment),
                }
                if current_segment
                else None
            ),
            "last_confirmation": self.navigation_state["last_confirmation"],
            "route_text": self._format_route_sequence(route_segments) if route_segments else None,
            "waypoints": [
                self._serialize_navigation_point(self.navigation_points_by_id.get(point_id))
                for point_id in self.navigation_state["waypoint_point_ids"]
                if point_id in self.navigation_points_by_id
            ],
            "service": (
                {
                    "service_id": self.navigation_state["service_id"],
                    "service_name": self.navigation_state["service_name"],
                    "ticket_number": self.navigation_state["ticket_number"],
                }
                if self.navigation_state["service_id"]
                else None
            ),
            "available_services": [
                {
                    "service_id": service["service_id"],
                    "service_name": service["service_name"],
                    "destination_point_id": service["destination_point_id"],
                }
                for service in self.navigation_services.values()
            ],
            "blocked_points": [
                self._serialize_navigation_point(self.navigation_points_by_id.get(point_id))
                for point_id in self._blocked_point_ids()
                if point_id in self.navigation_points_by_id
            ],
            "points": [
                self._serialize_navigation_point(point) for point in self.navigation_points
            ],
            "route": route,
            "confirmed_points": [
                self._serialize_navigation_point(self.navigation_points_by_id.get(point_id))
                for point_id in self.navigation_state["confirmed_point_ids"]
                if point_id in self.navigation_points_by_id
            ],
        }

    def _serialize_navigation_point(self, point: dict[str, Any] | None) -> dict[str, Any] | None:
        if point is None:
            return None

        confirmation = point.get("confirmation", {})
        methods = []
        if confirmation.get("rfid_device_id") is not None:
            methods.append("rfid")
        if confirmation.get("face_device_id") is not None:
            methods.append("face")
        if not methods:
            methods = ["rfid", "face"]

        return {
            "point_id": point["point_id"],
            "name": point["name"],
            "device_id": point["device_id"],
            "device_type": point["device_type"],
            "frequency_hz": point["frequency_hz"],
            "duration_ms": point["duration_ms"],
            "color": point.get("color"),
            "ring_index": point["ring_index"],
            "confirmation_methods": methods,
        }

    def _build_step_prompt(self, segment: dict[str, str], lead: str = "Дальше") -> str:
        to_point = self.navigation_points_by_id[segment["to_point_id"]]
        prompt = f"{lead} двигайтесь {self._format_route_segment(segment, short=True)}."
        if to_point.get("device_id") is not None:
            if to_point.get("device_type") == "type2" and to_point.get("color"):
                prompt += f" Ориентир: подсветка точки {to_point['name']}."
            else:
                prompt += f" Ориентир: звуковой сигнал точки {to_point['name']}."
        return prompt

    def _format_route_sequence(self, route_segments: list[dict[str, str]]) -> str:
        display_segments = self._collapse_route_segments(route_segments)
        return ", затем ".join(
            self._format_route_segment(segment, short=True)
            for segment in display_segments
        )

    def _format_route_segment(self, segment: dict[str, str], short: bool = False) -> str:
        from_point = self.navigation_points_by_id[segment["from_point_id"]]
        to_point = self.navigation_points_by_id[segment["to_point_id"]]
        spoken = self._connection_type_spoken(segment["connection_type"])
        if short:
            if segment["connection_type"] == "bus" and segment.get("via_point_ids"):
                return f"{spoken} по кольцу до {self._spoken_point_name(to_point)}"
            return f"{spoken} до {self._spoken_point_name(to_point)}"
        return (
            f"{self._connection_type_label(segment['connection_type'])}: "
            f"{from_point['name']} -> {to_point['name']}"
        )

    def _spoken_point_name(self, point: dict[str, Any]) -> str:
        name = point["name"]
        lowered = name.lower()
        if lowered.startswith("точка "):
            return f"точки {name.split(' ', 1)[1]}"
        return name

    def _next_ticket_number(self, queue_prefix: str) -> str:
        self._service_ticket_counter += 1
        return f"{queue_prefix}{self._service_ticket_counter:03d}"

    def _connection_type_label(self, connection_type: str) -> str:
        return CONNECTION_TYPE_LABELS.get(connection_type, "Неизвестная связь")

    def _connection_type_spoken(self, connection_type: str) -> str:
        return CONNECTION_TYPE_SPOKEN.get(connection_type, "по маршруту")

    def _append_navigation_log(self, action: str, payload: dict[str, Any]) -> None:
        self._append_log(
            {
                "category": "navigation",
                "timestamp": _now_iso(),
                "payload": {
                    "action": action,
                    **payload,
                },
            }
        )

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

    def _apply_event_locally(self, event: ForwardedEvent) -> None:
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
