import asyncio
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.services.local_state import LocalState


def build_client():
    os.environ["ENABLE_CITY_POLLING"] = "false"
    os.environ["TTS_ENABLED"] = "false"
    os.environ["TEXT_GENERATION_ENABLED"] = "false"
    runtime_dir = Path(tempfile.mkdtemp(prefix="ntoo-runtime-"))
    os.environ["CITY_RECEIVE_LOG_PATH"] = str(
        Path(tempfile.mkdtemp(prefix="ntoo-city-log-")) / "city-receive-log.txt"
    )
    os.environ["DATA_DIR"] = str(runtime_dir)
    os.environ["FACES_DIR"] = str(runtime_dir / "faces")
    os.environ["MODELS_DIR"] = str(runtime_dir / "models")
    os.environ["SNAPSHOT_DIR"] = str(runtime_dir / "snapshots")
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


def test_compat_event_endpoint_uses_existing_event_chain():
    app, fake_send_event, sent_events = build_client()
    with TestClient(app) as client:
        client.app.state.city_client.send_event = fake_send_event
        response = client.post("/event", json={"type": 4, "device_id": 10, "rfid_code": "legacy-card"})

        assert response.status_code == 200
        assert response.json()["accepted"] is True
        assert sent_events == [{"type": 4, "device_id": 10, "rfid_code": "legacy-card"}]


def test_compat_device_command_endpoint_keeps_legacy_shape():
    app, fake_send_event, _ = build_client()
    with TestClient(app) as client:
        client.app.state.city_client.send_event = fake_send_event
        start_response = client.post(
            "/api/navigation/start",
            json={"start_point_id": "point-1", "destination_point_id": "point-7"},
        )
        assert start_response.status_code == 200

        legacy_response = client.get("/devices/type1/10/command")
        assert legacy_response.status_code == 200
        legacy_command = legacy_response.json()
        assert legacy_command["active"] is True
        assert legacy_command["command_type"] == "type1_sound"
        assert legacy_command["payload"]["frequency_hz"] > 0
        assert legacy_command["payload"]["duration_ms"] > 0
        assert legacy_command["updated_at"] is not None

        current_response = client.get("/api/devices/10/command?device_type=type1")
        assert current_response.status_code == 200
        assert current_response.json()["active"] is True


def test_compat_heartbeat_updates_board_status():
    app, _, _ = build_client()
    with TestClient(app) as client:
        response = client.post(
            "/devices/heartbeat",
            json={
                "board_type": "type1_sound",
                "device_id": 10,
                "firmware": "device_node_mcp4725",
                "ip_address": "10.0.0.15",
            },
        )

        assert response.status_code == 200
        assert response.json()["board_key"] == "type1_sound:10"
        assert response.json()["online"] is True

        status = client.get("/api/device-status").json()
        assert status[0]["board_key"] == "type1_sound:10"
        assert status[0]["firmware"] == "device_node_mcp4725"


def test_esp32_cam_frame_endpoint_does_not_bypass_event_chain():
    app, fake_send_event, sent_events = build_client()
    with TestClient(app) as client:
        client.app.state.city_client.send_event = fake_send_event
        response = client.post(
            "/devices/esp32-cam/4/frame",
            content=b"not-a-real-jpeg",
            headers={"Content-Type": "image/jpeg"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["prediction"]["matched"] is False
        assert payload["dispatched"] == []
        assert sent_events == []

        status = client.get("/api/faces/status").json()
        assert len(status["snapshots"]) == 1


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
        assert "тёплую куртку" in data["recommendation"]["text"]
        assert data["recommendation"]["temperature_c"] == 2


def test_city_state_collection_tracks_snapshots_and_feed():
    app, _, _ = build_client()
    payload = {
        "voice_queue": [{"text": "city says hello", "ts": 10}],
        "devices_type1": {
            "5": {
                "duration_ms": 1000,
                "frequency_hz": 300,
                "ts": 10,
            }
        },
        "devices_type2": {
            "33": {
                "color": {"r": 255, "g": 120, "b": 0},
                "ts": 10,
            }
        },
        "buses": {
            "1": {
                "current_stop": 12,
                "timestamp": 1234567890,
            }
        },
        "events": {
            "rfid": [{"device_id": 5, "rfid_code": "ABC123", "ts": 10}],
            "face": [{"device_id": 5, "user_id": "SpiderMan", "confidence": 0.95, "ts": 10}],
        },
        "obstacles": [
            {
                "location_id": 12,
                "obstacle_type": "construction",
                "reroute_required": True,
                "message": "Go around on the right",
                "ts": 10,
            }
        ],
    }

    with TestClient(app) as client:
        asyncio.run(client.app.state.local_state.refresh_city_state(payload))

        state = client.get("/api/state").json()
        assert state["city"]["collector"]["snapshots_seen"] == 1
        assert state["city"]["collector"]["updates_seen"] >= 6
        assert any(item["path"] == "voice_queue" for item in state["city_updates_preview"])

        raw = client.get("/api/city/raw?snapshots=3").json()
        assert raw["raw"]["events"]["rfid"][0]["rfid_code"] == "ABC123"
        assert raw["snapshots"][0]["summary"]["bus_records"] == 1

        feed = client.get("/api/city/feed?limit=50").json()
        assert feed["collector"]["updates_seen"] >= 6
        assert any(item["path"] == "devices_type1.5" for item in feed["updates"])
        assert any(item["path"] == "events.face" and item["kind"] == "list_item" for item in feed["updates"])
        assert feed["collector"]["receive_log_entries"] == 1

        log_path = client.app.state.local_state.city_receive_log_path
        assert log_path is not None
        log_text = log_path.read_text(encoding="utf-8")
        assert "kind: snapshot" in log_text
        assert "updates_count:" in log_text
        assert "voice_queue" in log_text


def test_repeated_city_snapshot_does_not_duplicate_updates():
    app, _, _ = build_client()
    payload = {
        "voice_queue": [{"text": "once", "ts": 99}],
        "devices_type1": {"5": {"duration_ms": 500, "frequency_hz": 100, "ts": 99}},
    }

    with TestClient(app) as client:
        asyncio.run(client.app.state.local_state.refresh_city_state(payload))
        first = client.get("/api/city/feed?limit=50").json()

        asyncio.run(client.app.state.local_state.refresh_city_state(payload))
        second = client.get("/api/city/feed?limit=50").json()

        assert first["collector"]["updates_seen"] == second["collector"]["updates_seen"]
        assert second["collector"]["snapshots_seen"] == 2


def test_city_receive_log_records_errors():
    app, _, _ = build_client()

    with TestClient(app) as client:
        asyncio.run(client.app.state.local_state.mark_city_error("timeout"))

        log_path = client.app.state.local_state.city_receive_log_path
        assert log_path is not None
        log_text = log_path.read_text(encoding="utf-8")
        assert "kind: error" in log_text
        assert "timeout" in log_text


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
        assert "Маршрут до точки 7 готов." in sent_events[0]["text"]
        assert "Стартуем от Точка 1." in sent_events[0]["text"]
        assert "Сначала двигайтесь пешком до точки 10." in sent_events[0]["text"]
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
        assert rfid_data["followups"][1]["event"]["device_id"] == 102

        face_response = client.post(
            "/api/events",
            json={"type": 6, "device_id": 2, "user_id": "SpiderMan", "confidence": 0.98},
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
        assert "Дальше двигайтесь пешком до точки 9." in sent_events[3]["text"]
        assert sent_events[4]["type"] == 2
        assert sent_events[4]["device_id"] == 102
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
        assert {"to_point_id": "point-41", "connection_type": "crosswalk"} in graph["point-30"]
        assert {"to_point_id": "point-42", "connection_type": "crosswalk"} in graph["point-29"]

        assert {"to_point_id": "point-13", "connection_type": "crosswalk"} not in graph["point-9"]
        assert {"to_point_id": "point-14", "connection_type": "crosswalk"} not in graph["point-10"]
        assert {"to_point_id": "point-15", "connection_type": "crosswalk"} not in graph["point-12"]
        assert {"to_point_id": "point-16", "connection_type": "crosswalk"} not in graph["point-11"]
        assert {"to_point_id": "point-19", "connection_type": "crosswalk"} not in graph["point-31"]
        assert {"to_point_id": "point-20", "connection_type": "crosswalk"} not in graph["point-32"]
        assert {"to_point_id": "point-41", "connection_type": "crosswalk"} not in graph["point-29"]
        assert {"to_point_id": "point-42", "connection_type": "crosswalk"} not in graph["point-30"]


def test_navigation_accepts_camera_ids_for_mapped_points():
    app, fake_send_event, sent_events = build_client()
    with TestClient(app) as client:
        client.app.state.city_client.send_event = fake_send_event

        state = client.get("/api/state").json()
        points = {point["point_id"]: point for point in state["navigation"]["points"]}
        assert points["point-14"]["device_id"] == 101
        assert points["point-14"]["confirmation"]["rfid_device_id"] == 101
        assert points["point-14"]["confirmation"]["face_device_id"] == 1
        assert points["point-9"]["device_id"] == 102
        assert points["point-6"]["device_id"] == 103
        assert points["point-8"]["device_id"] == 104

        start_response = client.post(
            "/api/navigation/start",
            json={"start_point_id": "point-9", "destination_point_id": "point-16"},
        )
        assert start_response.status_code == 200
        start_data = start_response.json()
        assert start_data["navigation"]["current_point"]["point_id"] == "point-9"
        assert start_data["navigation"]["current_point"]["device_id"] == 102
        assert [event["type"] for event in sent_events] == [1, 2]
        assert sent_events[1]["device_id"] == 102

        rfid_with_camera_id_response = client.post(
            "/api/events",
            json={"type": 4, "device_id": 1, "rfid_code": "camera-id-is-not-rfid"},
        )
        assert rfid_with_camera_id_response.status_code == 200
        assert rfid_with_camera_id_response.json()["followups"] == []
        state = client.get("/api/state").json()
        assert state["navigation"]["current_point"]["point_id"] == "point-9"

        start_confirm_response = client.post(
            "/api/events",
            json={"type": 6, "device_id": 2, "user_id": "SpiderMan", "confidence": 0.98},
        )
        assert start_confirm_response.status_code == 200
        start_confirm_data = start_confirm_response.json()
        assert len(start_confirm_data["followups"]) == 2
        assert start_confirm_data["followups"][1]["event"]["device_id"] == 101
        state = client.get("/api/state").json()
        assert state["navigation"]["current_point"]["point_id"] == "point-14"

        confirm_response = client.post(
            "/api/events",
            json={"type": 6, "device_id": 1, "user_id": "SpiderMan", "confidence": 0.98},
        )
        assert confirm_response.status_code == 200
        confirm_data = confirm_response.json()
        assert len(confirm_data["followups"]) == 2

        state = client.get("/api/state").json()
        assert state["navigation"]["last_confirmation"]["point_id"] == "point-14"
        assert state["navigation"]["last_confirmation"]["device_id"] == 1
        assert state["navigation"]["last_confirmation"]["type"] == "face"
        assert state["navigation"]["current_point"]["point_id"] == "point-15"


def test_navigation_verifies_start_before_next_point_signal():
    app, _, sent_events = build_client()
    with TestClient(app) as client:
        observed_city_send_points = []

        async def observing_send_event(payload):
            if payload.get("type") == 6:
                snapshot = await client.app.state.local_state.snapshot()
                current_point = snapshot["navigation"]["current_point"]
                observed_city_send_points.append(
                    current_point["point_id"] if current_point else None
                )
            sent_events.append(payload)
            return {
                "ok": True,
                "status_code": 200,
                "response_text": "ok",
                "latency_ms": 1.0,
            }

        client.app.state.city_client.send_event = observing_send_event

        start_response = client.post(
            "/api/navigation/start",
            json={"start_point_id": "point-9", "destination_point_id": "point-8"},
        )
        assert start_response.status_code == 200
        start_data = start_response.json()
        assert start_data["navigation"]["current_point"]["point_id"] == "point-9"
        assert start_data["navigation"]["current_point"]["confirmation"]["face_device_id"] == 2
        assert [event["type"] for event in sent_events] == [1, 2]
        assert sent_events[1] == {
            "type": 2,
            "device_id": 102,
            "frequency_hz": 1100,
            "duration_ms": 1000,
        }

        start_confirm_response = client.post(
            "/api/events",
            json={"type": 6, "device_id": 2, "user_id": "SpiderMan", "confidence": 0.98},
        )
        assert start_confirm_response.status_code == 200
        start_confirm_data = start_confirm_response.json()
        assert len(start_confirm_data["followups"]) == 2
        assert start_confirm_data["followups"][1]["event"] == {
            "type": 2,
            "device_id": 104,
            "frequency_hz": 1900,
            "duration_ms": 1000,
        }
        assert observed_city_send_points == ["point-8"]

        state = client.get("/api/state").json()
        assert state["navigation"]["current_point"]["point_id"] == "point-8"
        assert [point["point_id"] for point in state["navigation"]["confirmed_points"]] == ["point-9"]

        finish_response = client.post(
            "/api/events",
            json={"type": 6, "device_id": 4, "user_id": "SpiderMan", "confidence": 0.98},
        )
        assert finish_response.status_code == 200
        finish_data = finish_response.json()
        assert len(finish_data["followups"]) == 1
        assert "Маршрут завершён" in finish_data["followups"][0]["event"]["text"]

        state = client.get("/api/state").json()
        assert state["navigation"]["status"] == "completed"
        assert [point["point_id"] for point in state["navigation"]["confirmed_points"]] == [
            "point-9",
            "point-8",
        ]


def test_navigation_collapses_bus_ring_into_single_route_step():
    app, fake_send_event, _ = build_client()
    with TestClient(app) as client:
        client.app.state.city_client.send_event = fake_send_event
        response = client.post(
            "/api/navigation/start",
            json={"start_point_id": "point-9", "destination_point_id": "point-16"},
        )
        assert response.status_code == 200
        data = response.json()

        assert [step["to_point"]["point_id"] for step in data["navigation"]["route"]] == [
            "point-14",
            "point-16",
        ]
        assert data["navigation"]["route"][1]["connection_type"] == "bus"
        assert data["navigation"]["route"][1]["via_point_ids"] == ["point-15"]
        assert "по кольцу" in data["navigation"]["route_text"]


def test_navigation_keeps_bus_ring_progression_between_hidden_stops():
    app, fake_send_event, sent_events = build_client()
    with TestClient(app) as client:
        client.app.state.city_client.send_event = fake_send_event
        start_response = client.post(
            "/api/navigation/start",
            json={"start_point_id": "point-9", "destination_point_id": "point-16"},
        )
        assert start_response.status_code == 200

        start_confirm_response = client.post(
            "/api/events",
            json={"type": 6, "device_id": 2, "user_id": "SpiderMan", "confidence": 0.98},
        )
        assert start_confirm_response.status_code == 200
        start_confirm_data = start_confirm_response.json()
        assert len(start_confirm_data["followups"]) == 2
        assert start_confirm_data["followups"][1]["event"]["device_id"] == 101

        confirm_response = client.post(
            "/api/events",
            json={"type": 4, "device_id": 101, "rfid_code": "ring-card"},
        )
        assert confirm_response.status_code == 200
        data = confirm_response.json()
        assert len(data["followups"]) == 2
        assert data["followups"][1]["event"]["type"] == 2
        assert data["followups"][1]["event"]["device_id"] == 15

        state = client.get("/api/state").json()
        assert state["navigation"]["current_point"]["point_id"] == "point-15"
        assert [step["to_point"]["point_id"] for step in state["navigation"]["route"]] == [
            "point-14",
            "point-16",
        ]
        assert state["navigation"]["route"][1]["status"] == "active"

        assert sent_events[-2]["type"] == 1
        assert "до точки 15" in sent_events[-2]["text"]
        assert sent_events[-1]["type"] == 2
        assert sent_events[-1]["device_id"] == 15


def test_navigation_reroutes_when_obstacle_hits_collapsed_bus_segment():
    app, fake_send_event, _ = build_client()
    with TestClient(app) as client:
        client.app.state.city_client.send_event = fake_send_event

        start_response = client.post(
            "/api/navigation/start",
            json={"start_point_id": "point-9", "destination_point_id": "point-16"},
        )
        assert start_response.status_code == 200
        start_data = start_response.json()
        assert start_data["navigation"]["route"][1]["via_point_ids"] == ["point-15"]

        obstacle_response = client.post(
            "/api/events",
            json={
                "type": 5,
                "location_id": 15,
                "obstacle_type": "construction",
                "reroute_required": True,
                "message": "Blocked bus stop",
            },
        )
        assert obstacle_response.status_code == 200
        data = obstacle_response.json()
        assert len(data["followups"]) == 2
        assert data["followups"][1]["event"]["type"] == 2
        assert data["followups"][1]["event"]["device_id"] == 10

        state = client.get("/api/state").json()
        assert [step["to_point"]["point_id"] for step in state["navigation"]["route"]] == [
            "point-10",
            "point-13",
            "point-16",
        ]
        assert state["navigation"]["route"][2]["via_point_ids"] == ["point-17"]
        assert [point["point_id"] for point in state["navigation"]["blocked_points"]] == ["point-15"]


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


def test_navigation_excludes_start_and_destination_from_waypoints():
    app, fake_send_event, _ = build_client()
    with TestClient(app) as client:
        client.app.state.city_client.send_event = fake_send_event
        response = client.post(
            "/api/navigation/start",
            json={
                "start_point_id": "point-1",
                "destination_point_id": "point-7",
                "waypoint_point_ids": ["point-1", "point-12", "point-7"],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert [point["point_id"] for point in data["navigation"]["waypoints"]] == ["point-12"]
        assert all(
            point["point_id"] not in {"point-1", "point-7"}
            for point in data["navigation"]["waypoints"]
        )


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
