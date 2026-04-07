class MockDeviceClient:
    def __init__(self) -> None:
        self.commands: dict[str, list[dict]] = {}

    def queue_signal(self, device_id: str, payload: dict) -> None:
        self.commands.setdefault(device_id, []).append(payload)

