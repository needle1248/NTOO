class MockCityClient:
    def __init__(self) -> None:
        self.sent_events: list[dict] = []

    async def send(self, payload: dict) -> None:
        self.sent_events.append(payload)

