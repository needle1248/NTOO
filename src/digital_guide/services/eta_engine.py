from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime

from digital_guide.core.models import BusState, RouteGraph


class ETAEngine:
    def __init__(self, graph: RouteGraph, rolling_window: int = 5) -> None:
        self.graph = graph
        self.ring_stop_order = graph.stop_mappings
        self.rolling_window = rolling_window
        self.bus_updates: dict[str, list[tuple[int, datetime]]] = defaultdict(list)
        self.segment_averages: dict[str, dict[tuple[int, int], deque[float]]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=self.rolling_window))
        )
        self.lap_averages: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=self.rolling_window))

    def update_bus(self, bus_id: str, ring_id: str, current_stop: int, timestamp: datetime) -> BusState:
        history = self.bus_updates[bus_id]
        stop_order = self.ring_stop_order[ring_id]
        if history:
            previous_stop, previous_ts = history[-1]
            delta = max((timestamp - previous_ts).total_seconds(), 1.0)
            path = self._expanded_path(stop_order, previous_stop, current_stop)
            share = delta / max(len(path), 1)
            cursor = previous_stop
            for next_stop in path:
                self.segment_averages[ring_id][(cursor, next_stop)].append(share)
                cursor = next_stop
            if stop_order.index(current_stop) <= stop_order.index(previous_stop):
                lap_time = self._lap_time_from_history(history + [(current_stop, timestamp)])
                if lap_time:
                    self.lap_averages[ring_id].append(lap_time)
        history.append((current_stop, timestamp))
        history[:] = history[-20:]
        lap_avg = self._average(self.lap_averages[ring_id])
        return BusState(
            bus_id=bus_id,
            ring_id=ring_id,
            current_stop=current_stop,
            timestamp=timestamp,
            last_seen=timestamp,
            lap_time_seconds=self.lap_averages[ring_id][-1] if self.lap_averages[ring_id] else None,
            rolling_lap_average_seconds=lap_avg,
            eta_by_stop=self.estimate_all_stops(ring_id, current_stop, lap_avg),
        )

    def estimate_eta(self, ring_id: str, current_stop: int, target_stop: int, fallback_lap: float | None = None) -> float:
        stop_order = self.ring_stop_order[ring_id]
        path = self._expanded_path(stop_order, current_stop, target_stop)
        lap_avg = fallback_lap or self._average(self.lap_averages[ring_id]) or float(len(stop_order) * 20)
        fallback_segment = lap_avg / max(len(stop_order), 1)
        eta = 0.0
        cursor = current_stop
        for next_stop in path:
            eta += self._average(self.segment_averages[ring_id][(cursor, next_stop)]) or fallback_segment
            cursor = next_stop
        return eta

    def estimate_all_stops(self, ring_id: str, current_stop: int, fallback_lap: float | None = None) -> dict[int, float]:
        return {
            stop: self.estimate_eta(ring_id=ring_id, current_stop=current_stop, target_stop=stop, fallback_lap=fallback_lap)
            for stop in self.ring_stop_order[ring_id]
        }

    def _expanded_path(self, stop_order: list[int], current_stop: int, target_stop: int) -> list[int]:
        if current_stop == target_stop:
            return []
        current_idx = stop_order.index(current_stop)
        target_idx = stop_order.index(target_stop)
        if target_idx > current_idx:
            return stop_order[current_idx + 1 : target_idx + 1]
        return stop_order[current_idx + 1 :] + stop_order[: target_idx + 1]

    def _lap_time_from_history(self, history: list[tuple[int, datetime]]) -> float | None:
        current_stop, current_ts = history[-1]
        for stop, ts in reversed(history[:-1]):
            if stop == current_stop:
                return max((current_ts - ts).total_seconds(), 1.0)
        return None

    @staticmethod
    def _average(values: deque[float]) -> float | None:
        if not values:
            return None
        return sum(values) / len(values)

