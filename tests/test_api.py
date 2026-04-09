import os

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


def build_client():
    os.environ["ENABLE_CITY_POLLING"] = "false"
    get_settings.cache_clear()
    app = create_app()

    async def fake_send_event(payload):
        return {
            "ok": True,
            "status_code": 200,
            "response_text": "ok",
            "latency_ms": 1.0,
        }

    return app, fake_send_event


def test_type1_event_is_accepted_and_logged():
    app, fake_send_event = build_client()
    with TestClient(app) as client:
        client.app.state.city_client.send_event = fake_send_event
        response = client.post("/api/events", json={"type": 1, "text": "Тест озвучки"})
        assert response.status_code == 200
        data = response.json()
        assert data["accepted"] is True

        state = client.get("/api/state").json()
        assert state["metrics"]["forwarded_ok"] == 1
        assert state["voice_queue"][0]["text"] == "Тест озвучки"


def test_environment_creates_clothing_recommendation():
    app, fake_send_event = build_client()
    with TestClient(app) as client:
        client.app.state.city_client.send_event = fake_send_event
        response = client.post(
            "/api/sensors/environment",
            json={
                "temperature_c": 2,
                "humidity_percent": 85,
                "pressure_hpa": 985,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["recommendation"]["mode"] == "cold"
        assert "тёплая куртка" in data["recommendation"]["text"]
