import asyncio
import os

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.services.local_state import LocalState


def build_client():
    os.environ["ENABLE_CITY_POLLING"] = "false"
    get_settings.cache_clear()
    app = create_app()
    sent_events = []

    async def fake_send_event(payload):
        sent_events.append(payload)
        return {
            "ok": True,
            "status_code": 200,
            "response_text": "ok",
            "latency_ms": 1.0,
        }

    return app, fake_send_event, sent_events


def test_type1_event_is_accepted_and_logged():
    app, fake_send_event, _ = build_client()
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
    app, fake_send_event, _ = build_client()
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


def test_navigation_start_dispatches_voice_and_first_sound():
    app, fake_send_event, sent_events = build_client()
    with TestClient(app) as client:
        client.app.state.city_client.send_event = fake_send_event
        response = client.post(
            "/api/navigation/start",
            json={"start_point_id": "point-1", "destination_point_id": "point-7"},
        )
        assert response.status_code == 200
        data = response.json()

        assert data["ok"] is True
        assert data["navigation"]["active"] is True
        assert data["navigation"]["current_point"]["point_id"] == "point-10"
        assert data["navigation"]["destination"]["point_id"] == "point-7"
        assert data["navigation"]["route_text"] == (
            "пешком до точки 10, затем пешком до точки 9, "
            "затем пешком до точки 8, затем пешком до точки 7"
        )
        assert [step["to_point"]["point_id"] for step in data["navigation"]["route"]] == [
            "point-10",
            "point-9",
            "point-8",
            "point-7",
        ]
        assert data["navigation"]["route"][0]["connection_type"] == "walk"
        assert data["navigation"]["route"][0]["connection_type_label"] == "Пешая связь"

        assert sent_events[0]["type"] == 1
        assert "Маршрут до точки 7 построен." in sent_events[0]["text"]
        assert "Маршрут: пешком до точки 10" in sent_events[0]["text"]
        assert sent_events[1] == {
            "type": 2,
            "device_id": 10,
            "frequency_hz": 1000,
            "duration_ms": 1000,
        }


def test_navigation_advances_on_rfid_and_face_confirmation():
    app, fake_send_event, sent_events = build_client()
    with TestClient(app) as client:
        client.app.state.city_client.send_event = fake_send_event

        start_response = client.post(
            "/api/navigation/start",
            json={"start_point_id": "point-1", "destination_point_id": "point-9"},
        )
        assert start_response.status_code == 200

        rfid_response = client.post(
            "/api/events",
            json={"type": 4, "device_id": 777, "rfid_code": "test-card"},
        )
        assert rfid_response.status_code == 200
        rfid_data = rfid_response.json()
        assert len(rfid_data["followups"]) == 2
        assert rfid_data["followups"][1]["event"]["device_id"] == 9

        face_response = client.post(
            "/api/events",
            json={"type": 6, "device_id": 888, "user_id": "SpiderMan", "confidence": 0.98},
        )
        assert face_response.status_code == 200
        face_data = face_response.json()
        assert len(face_data["followups"]) == 1
        assert "Маршрут завершён" in face_data["followups"][0]["event"]["text"]

        state = client.get("/api/state").json()
        assert state["navigation"]["active"] is False
        assert state["navigation"]["status"] == "completed"
        assert [point["point_id"] for point in state["navigation"]["confirmed_points"]] == [
            "point-10",
            "point-9",
        ]

        assert sent_events[3]["type"] == 1
        assert "пешком до точки 9" in sent_events[3]["text"]
        assert sent_events[4]["type"] == 2
        assert sent_events[4]["device_id"] == 9
        assert sent_events[6]["type"] == 1


def test_navigation_crosswalks_only_use_local_team_stops():
    app, _, _ = build_client()
    with TestClient(app) as client:
        graph = client.app.state.local_state.navigation_graph

        assert {"to_point_id": "point-13", "connection_type": "crosswalk"} in graph["point-10"]
        assert {"to_point_id": "point-14", "connection_type": "crosswalk"} in graph["point-9"]
        assert {"to_point_id": "point-16", "connection_type": "crosswalk"} in graph["point-12"]
        assert {"to_point_id": "point-15", "connection_type": "crosswalk"} in graph["point-11"]
        assert {"to_point_id": "point-20", "connection_type": "crosswalk"} in graph["point-31"]
        assert {"to_point_id": "point-19", "connection_type": "crosswalk"} in graph["point-32"]
        assert {"to_point_id": "point-33", "connection_type": "crosswalk"} in graph["point-30"]
        assert {"to_point_id": "point-34", "connection_type": "crosswalk"} in graph["point-29"]

        assert {"to_point_id": "point-13", "connection_type": "crosswalk"} not in graph["point-9"]
        assert {"to_point_id": "point-14", "connection_type": "crosswalk"} not in graph["point-10"]
        assert {"to_point_id": "point-15", "connection_type": "crosswalk"} not in graph["point-12"]
        assert {"to_point_id": "point-16", "connection_type": "crosswalk"} not in graph["point-11"]
        assert {"to_point_id": "point-19", "connection_type": "crosswalk"} not in graph["point-31"]
        assert {"to_point_id": "point-20", "connection_type": "crosswalk"} not in graph["point-32"]
        assert {"to_point_id": "point-33", "connection_type": "crosswalk"} not in graph["point-29"]
        assert {"to_point_id": "point-34", "connection_type": "crosswalk"} not in graph["point-30"]


def test_navigation_supports_multiple_waypoints():
    app, fake_send_event, _ = build_client()
    with TestClient(app) as client:
        client.app.state.city_client.send_event = fake_send_event
        response = client.post(
            "/api/navigation/start",
            json={
                "start_point_id": "point-1",
                "destination_point_id": "point-7",
                "waypoint_point_ids": ["point-12", "point-11"],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert [point["point_id"] for point in data["navigation"]["waypoints"]] == [
            "point-12",
            "point-11",
        ]
        assert [step["to_point"]["point_id"] for step in data["navigation"]["route"]] == [
            "point-4",
            "point-12",
            "point-11",
            "point-7",
        ]


def test_navigation_reroutes_when_obstacle_blocks_active_path():
    app, fake_send_event, _ = build_client()
    with TestClient(app) as client:
        client.app.state.city_client.send_event = fake_send_event

        start_response = client.post(
            "/api/navigation/start",
            json={"start_point_id": "point-1", "destination_point_id": "point-7"},
        )
        assert start_response.status_code == 200

        obstacle_response = client.post(
            "/api/events",
            json={
                "type": 5,
                "location_id": 10,
                "obstacle_type": "construction",
                "reroute_required": True,
                "message": "На вашем маршруте строительные работы.",
            },
        )
        assert obstacle_response.status_code == 200
        data = obstacle_response.json()
        assert len(data["followups"]) == 2
        assert data["followups"][1]["event"]["type"] == 2
        assert data["followups"][1]["event"]["device_id"] == 4

        state = client.get("/api/state").json()
        assert state["navigation"]["current_point"]["point_id"] == "point-4"
        assert [point["point_id"] for point in state["navigation"]["blocked_points"]] == ["point-10"]
        assert [step["to_point"]["point_id"] for step in state["navigation"]["route"]] == [
            "point-4",
            "point-12",
            "point-11",
            "point-7",
        ]


def test_indoor_service_navigation_uses_type2_lights():
    team_profile = {
        "devices": {"type1_ids": [], "type2_ids": [201, 202], "vibration_device_id": None},
        "signal": {
            "type1": {"frequency_hz": 1000, "duration_ms": 1000},
            "type2": {"color": {"r": 0, "g": 120, "b": 255}},
        },
        "bus": {"target_stop_id": None, "route_by_bus": {"default": []}},
        "scenario": {
            "start_point_id": "mfc-entry",
            "points": [
                {"point_id": "mfc-entry", "name": "Вход МФЦ", "device_type": "type2", "device_id": 201},
                {"point_id": "mfc-window-1", "name": "Окно 1", "device_type": "type2", "device_id": 202},
            ],
            "edges": [
                {"from": "mfc-entry", "to": "mfc-window-1", "connection_type": "walk"},
            ],
            "services": [
                {
                    "service_id": "passport",
                    "service_name": "Паспортные услуги",
                    "destination_point_id": "mfc-window-1",
                    "queue_prefix": "A",
                },
            ],
        },
    }
    state = LocalState(team_profile)

    events = asyncio.run(state.start_navigation(service_id="passport"))
    snapshot = asyncio.run(state.snapshot())

    assert len(events) == 2
    assert events[1].type == 3
    assert events[1].device_id == 202
    assert snapshot["navigation"]["service"]["service_id"] == "passport"
    assert snapshot["navigation"]["service"]["ticket_number"] == "A001"
    assert snapshot["navigation"]["current_point"]["device_type"] == "type2"
