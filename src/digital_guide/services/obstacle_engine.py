from __future__ import annotations

from digital_guide.core.models import ObstacleEvent
from digital_guide.core.state import AppStateStore


class ObstacleEngine:
    def __init__(self, store: AppStateStore) -> None:
        self.store = store

    def upsert(self, obstacle: ObstacleEvent) -> None:
        if obstacle.active:
            self.store.obstacles[obstacle.location_id] = obstacle
        else:
            self.store.obstacles.pop(obstacle.location_id, None)

    def blocked_nodes(self) -> set[int]:
        return {location_id for location_id, event in self.store.obstacles.items() if event.reroute_required and event.active}

    def route_impacted(self, route_nodes: list[int]) -> bool:
        blocked = self.blocked_nodes()
        return any(node_id in blocked for node_id in route_nodes)
