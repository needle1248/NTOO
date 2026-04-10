from __future__ import annotations

import statistics
import time
from collections import defaultdict, deque
from typing import Any


def _stringify_stop(value: Any) -> str:
    return str(value)


class BusTracker:
    def __init__(
        self,
        route_by_bus: dict[str, list[Any]] | None = None,
        default_segment_seconds: float = 25.0,
    ) -> None:
        self.route_by_bus = {
            str(bus_id): [_stringify_stop(stop) for stop in route]
            for bus_id, route in (route_by_bus or {}).items()
        }
        self.default_segment_seconds = default_segment_seconds
        self.last_seen: dict[str, dict[str, Any]] = {}
        self.segment_samples: dict[tuple[str, str, str], deque[float]] = defaultdict(
            lambda: deque(maxlen=25)
        )
        self.observed_routes: dict[str, list[str]] = defaultdict(list)
        self.lap_samples: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=10))
        self.cycle_started_at: dict[str, int] = {}

    def record_many(self, records: list[dict[str, Any]]) -> None:
        for record in sorted(records, key=lambda item: (str(item["bus_id"]), int(item["timestamp"]))):
            self.record(record)

    def record(self, record: dict[str, Any]) -> None:
        bus_id = str(record["bus_id"])
        current_stop = _stringify_stop(record["current_stop"])
        timestamp = int(record.get("timestamp") or time.time())

        observed_route = self.observed_routes[bus_id]
        if current_stop not in observed_route:
            observed_route.append(current_stop)

        previous = self.last_seen.get(bus_id)
        if previous and timestamp <= previous["timestamp"]:
            return

        if previous and current_stop != previous["current_stop"]:
            segment_key = (bus_id, previous["current_stop"], current_stop)
            self.segment_samples[segment_key].append(timestamp - previous["timestamp"])

        if bus_id not in self.cycle_started_at:
            self.cycle_started_at[bus_id] = timestamp
        elif previous and current_stop == observed_route[0] and len(observed_route) > 1:
            self.lap_samples[bus_id].append(timestamp - self.cycle_started_at[bus_id])
            self.cycle_started_at[bus_id] = timestamp

        self.last_seen[bus_id] = {
            "bus_id": bus_id,
            "current_stop": current_stop,
            "timestamp": timestamp,
        }

    def _get_route(self, bus_id: str) -> list[str]:
        route = self.route_by_bus.get(bus_id) or self.route_by_bus.get("default")
        if route:
            return route
        return self.observed_routes.get(bus_id, [])

    def _average_segment(self, bus_id: str, stop_from: str, stop_to: str) -> float:
        key = (bus_id, stop_from, stop_to)
        if self.segment_samples[key]:
            return statistics.fmean(self.segment_samples[key])

        any_bus_samples = [
            value
            for (sample_bus_id, sample_from, sample_to), samples in self.segment_samples.items()
            if sample_from == stop_from and sample_to == stop_to
            for value in samples
        ]
        if any_bus_samples:
            return statistics.fmean(any_bus_samples)

        all_bus_samples = [
            value
            for (sample_bus_id, _, _), samples in self.segment_samples.items()
            if sample_bus_id == bus_id
            for value in samples
        ]
        if all_bus_samples:
            return statistics.fmean(all_bus_samples)

        return self.default_segment_seconds

    def estimate_eta(self, bus_id: str, target_stop_id: Any) -> float | None:
        target_stop = _stringify_stop(target_stop_id)
        current = self.last_seen.get(bus_id)
        if not current:
            return None

        route = self._get_route(bus_id)
        if not route or target_stop not in route or current["current_stop"] not in route:
            return None
        if current["current_stop"] == target_stop:
            return 0.0

        eta = 0.0
        current_idx = route.index(current["current_stop"])
        for _ in range(len(route) - 1):
            next_idx = (current_idx + 1) % len(route)
            stop_from = route[current_idx]
            stop_to = route[next_idx]
            eta += self._average_segment(bus_id, stop_from, stop_to)
            if stop_to == target_stop:
                return eta
            current_idx = next_idx

        return None

    def baseline_eta(self, bus_id: str, target_stop_id: Any) -> float | None:
        route = self._get_route(bus_id)
        current = self.last_seen.get(bus_id)
        if not route or not current:
            return None

        if _stringify_stop(target_stop_id) not in route:
            return None

        lap_samples = self.lap_samples.get(bus_id)
        if not lap_samples:
            return self.estimate_eta(bus_id, target_stop_id)

        lap_time = statistics.fmean(lap_samples)
        average_segment = lap_time / max(len(route), 1)
        eta = self.estimate_eta(bus_id, target_stop_id)
        if eta is None:
            return None
        segments = max(round(eta / max(average_segment, 1)), 1)
        return average_segment * segments

    def snapshot(self, target_stop_id: Any | None = None) -> dict[str, Any]:
        buses: list[dict[str, Any]] = []
        best_eta = None
        best_baseline = None

        for bus_id, current in sorted(self.last_seen.items()):
            bus_snapshot = dict(current)
            bus_snapshot["known_route"] = self._get_route(bus_id)
            if target_stop_id is not None:
                bus_snapshot["eta_to_target_stop_seconds"] = self.estimate_eta(bus_id, target_stop_id)
                bus_snapshot["baseline_eta_to_target_stop_seconds"] = self.baseline_eta(
                    bus_id, target_stop_id
                )

                eta = bus_snapshot["eta_to_target_stop_seconds"]
                baseline = bus_snapshot["baseline_eta_to_target_stop_seconds"]
                if eta is not None and (best_eta is None or eta < best_eta):
                    best_eta = eta
                if baseline is not None and (best_baseline is None or baseline < best_baseline):
                    best_baseline = baseline

            buses.append(bus_snapshot)

        return {
            "target_stop_id": target_stop_id,
            "best_eta_seconds": best_eta,
            "baseline_eta_seconds": best_baseline,
            "buses": buses,
        }


def extract_bus_records(payload: Any) -> list[dict[str, Any]]:
    seen: dict[tuple[str, str, int], dict[str, Any]] = {}

    def walk(value: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            if "bus_id" in value and "current_stop" in value:
                bus_id = str(value["bus_id"])
                current_stop = _stringify_stop(value["current_stop"])
                timestamp = int(value.get("timestamp") or time.time())
                seen[(bus_id, current_stop, timestamp)] = {
                    "bus_id": bus_id,
                    "current_stop": current_stop,
                    "timestamp": timestamp,
                }
            elif len(path) >= 2 and path[-2] == "buses" and "current_stop" in value:
                bus_id = str(path[-1])
                current_stop = _stringify_stop(value["current_stop"])
                timestamp = int(value.get("timestamp") or time.time())
                seen[(bus_id, current_stop, timestamp)] = {
                    "bus_id": bus_id,
                    "current_stop": current_stop,
                    "timestamp": timestamp,
                }
            for key, nested in value.items():
                walk(nested, (*path, str(key)))
        elif isinstance(value, list):
            for item in value:
                walk(item, path)

    walk(payload)
    return list(seen.values())
