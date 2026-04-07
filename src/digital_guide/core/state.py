from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field

from digital_guide.core.models import AuditRecord, BusState, ObstacleEvent, RoutePlan, ScenarioState, TeamConfig, WeatherSnapshot
from digital_guide.services.persistence import JsonPersistence


@dataclass
class AppStateStore:
    team_config: TeamConfig
    logger: logging.Logger
    persistence: JsonPersistence
    devices: dict[str, dict] = field(default_factory=dict)
    buses: dict[str, BusState] = field(default_factory=dict)
    active_route: RoutePlan | None = None
    scenarios: dict[str, ScenarioState] = field(default_factory=dict)
    obstacles: dict[int, ObstacleEvent] = field(default_factory=dict)
    audit_log: deque[AuditRecord] = field(default_factory=lambda: deque(maxlen=1000))
    weather: WeatherSnapshot | None = None
    pending_city_events: deque[dict] = field(default_factory=deque)
    device_command_queue: dict[str, list[dict]] = field(default_factory=dict)
    last_recommendation_text: str | None = None
    last_congestion_warning: str | None = None

    def append_log(self, record: AuditRecord) -> None:
        self.audit_log.append(record)
        self.persistence.write_jsonl("audit", record.model_dump(mode="json"))

    def queue_city_event(self, payload: dict) -> None:
        self.pending_city_events.append(payload)

    def queue_device_command(self, device_id: str, payload: dict) -> None:
        self.device_command_queue.setdefault(device_id, []).append(payload)

    def pop_device_commands(self, device_id: str) -> list[dict]:
        return self.device_command_queue.pop(device_id, [])

    def flush(self) -> None:
        snapshot = {
            "devices": self.devices,
            "buses": {key: value.model_dump(mode="json") for key, value in self.buses.items()},
            "active_route": self.active_route.model_dump(mode="json") if self.active_route else None,
            "scenarios": {key: value.model_dump(mode="json") for key, value in self.scenarios.items()},
            "obstacles": {key: value.model_dump(mode="json") for key, value in self.obstacles.items()},
            "weather": self.weather.model_dump(mode="json") if self.weather else None,
            "last_recommendation_text": self.last_recommendation_text,
            "last_congestion_warning": self.last_congestion_warning,
        }
        self.persistence.write_json("state_snapshot.json", snapshot)
