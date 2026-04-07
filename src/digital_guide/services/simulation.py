from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from digital_guide.core.models import DeviceEventEnvelope
from digital_guide.core.state import AppStateStore
from digital_guide.services.eta_engine import ETAEngine


class SimulationService:
    def __init__(self, store: AppStateStore, logger: logging.Logger, scenario_engine, eta_engine: ETAEngine) -> None:
        self.store = store
        self.logger = logger
        self.scenario_engine = scenario_engine
        self.eta_engine = eta_engine
        self._tick = 0

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.store.team_config.simulation.loop_interval_seconds)
            self._tick += 1
            if self.store.team_config.simulation.auto_bus_updates:
                self.simulate_bus_update()

    def simulate_bus_update(self) -> None:
        ring_id = self.store.team_config.primary_ring
        stops = self.store.team_config.ring_stop_order[ring_id]
        stop = stops[self._tick % len(stops)]
        timestamp = datetime.now(timezone.utc) + timedelta(seconds=self._tick * 10)
        state = self.eta_engine.update_bus(bus_id=f"{ring_id}-bus-1", ring_id=ring_id, current_stop=stop, timestamp=timestamp)
        self.store.buses[state.bus_id] = state
        self.logger.info("simulation bus update", extra={"payload": state.model_dump(mode="json")})

    def simulate_point_confirmation(self, device_id: str, user_id: str) -> dict:
        payload = DeviceEventEnvelope(type=6, device_id=device_id, user_id=user_id, confidence=0.93)
        return self.scenario_engine.handle_device_event(payload)

    def simulate_obstacle(self, location_id: int, message: str) -> dict:
        payload = DeviceEventEnvelope(type=5, location_id=location_id, obstacle_type="construction", reroute_required=True, message=message)
        return self.scenario_engine.handle_device_event(payload)

    def simulate_distance_sensor(self, device_id: str, distance_cm: float) -> dict:
        payload = DeviceEventEnvelope(type=99, device_id=device_id, distance_cm=distance_cm)
        return self.scenario_engine.handle_device_event(payload)

