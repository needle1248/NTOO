from __future__ import annotations

import logging

from digital_guide.core.models import DeviceEventEnvelope
from digital_guide.core.state import AppStateStore


class DeviceGateway:
    def __init__(self, store: AppStateStore, logger: logging.Logger) -> None:
        self.store = store
        self.logger = logger

    def register_heartbeat(self, device_id: str, payload: dict) -> None:
        self.store.devices[device_id] = payload
        self.logger.info("device heartbeat", extra={"payload": payload})

    def queue_signal(self, device_id: str, payload: dict) -> None:
        self.store.queue_device_command(device_id, payload)
        self.logger.info("device command queued", extra={"payload": {"device_id": device_id, "command": payload}})

    def poll_commands(self, device_id: str) -> list[dict]:
        return self.store.pop_device_commands(device_id)

    def normalize_event(self, payload: DeviceEventEnvelope) -> dict:
        model = payload.model_dump(mode="json")
        self.logger.info("device event received", extra={"payload": model})
        return model

