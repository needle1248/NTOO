from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from digital_guide.core.models import (
    AuditRecord,
    BuildRouteRequest,
    DeviceEventEnvelope,
    ETAResponse,
    ObstacleEvent,
    RecommendationResponse,
    StartScenarioRequest,
    StopScenarioRequest,
    VoiceRouteParseRequest,
)

router = APIRouter()


def _public_team_state(request: Request) -> dict:
    team = request.app.state.store.team_config
    return {
        "team_id": team.team_id,
        "team_name": team.team_name,
        "server_name": team.team_name,
        "primary_ring": team.primary_ring,
        "user_id": team.user_id,
        "selected_confirmation_mode": team.selected_confirmation_mode.value,
        "face_threshold": team.face_threshold,
        "primary_signal_profile": team.primary_signal_profile,
        "simulation": team.simulation.model_dump(mode="json"),
        "ring_stop_order": team.ring_stop_order,
        "team_point_mapping": team.team_point_mapping,
        "city": {
            "enabled": team.city.enabled,
            "allow_outbound_events": team.city.allow_outbound_events,
            "base_url": team.city.base_url,
            "state_url": team.city.state_url,
            "event_path": team.city.event_path,
            "poll_interval_seconds": team.city.poll_interval_seconds,
            "timeout_seconds": team.city.timeout_seconds,
            "bus_ring_mapping": team.city.bus_ring_mapping,
            "stop_index_mapping": team.city.stop_index_mapping,
        },
        "deepseek": {
            "enabled": team.deepseek.enabled,
            "base_url": team.deepseek.base_url,
            "model": team.deepseek.model,
            "timeout_seconds": team.deepseek.timeout_seconds,
        },
    }


def _web_page(name: str) -> FileResponse:
    web_dir = Path(__file__).resolve().parents[1] / "web"
    return FileResponse(web_dir / name)


@router.get("/")
async def root() -> FileResponse:
    return _web_page("dashboard.html")


@router.get("/dashboard")
async def dashboard() -> FileResponse:
    return _web_page("dashboard.html")


@router.get("/devices")
async def devices_page() -> FileResponse:
    return _web_page("devices.html")


@router.get("/route-builder")
async def route_builder_page() -> FileResponse:
    return _web_page("route_builder.html")


@router.get("/transport")
async def transport_page() -> FileResponse:
    return _web_page("transport.html")


@router.get("/mfc")
async def mfc_page() -> FileResponse:
    return _web_page("mfc.html")


@router.get("/logs")
async def logs_page() -> FileResponse:
    return _web_page("logs.html")


@router.get("/health")
async def health(request: Request) -> dict:
    team = request.app.state.store.team_config
    return {"status": "ok", "team_id": team.team_id, "team_name": team.team_name, "server_name": team.team_name}


@router.get("/api/state")
async def get_state(request: Request) -> dict:
    store = request.app.state.store
    return {
        "team": _public_team_state(request),
        "devices": store.devices,
        "buses": [bus.model_dump(mode="json") for bus in store.buses.values()],
        "obstacles": [item.model_dump(mode="json") for item in store.obstacles.values()],
        "weather": store.weather.model_dump(mode="json") if store.weather else None,
        "active_route": store.active_route.model_dump(mode="json") if store.active_route else None,
        "scenarios": [item.model_dump(mode="json") for item in store.scenarios.values()],
        "recommendation": store.last_recommendation_text,
        "congestion_warning": store.last_congestion_warning,
    }


@router.get("/api/routes/current")
async def get_current_route(request: Request) -> dict:
    route = request.app.state.store.active_route
    return route.model_dump(mode="json") if route else {"route": None}


@router.post("/api/routes/build")
async def build_route(request: Request, payload: BuildRouteRequest) -> dict:
    try:
        route = request.app.state.route_engine.build_route(
            start_node=payload.start_node,
            goal_node=payload.goal_node,
            via_nodes=payload.via_nodes,
            blocked_nodes=request.app.state.obstacle_engine.blocked_nodes(),
            scenario_kind=payload.scenario_kind,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    request.app.state.store.active_route = route
    request.app.state.store.append_log(
        AuditRecord(
            event="route_built",
            message="Маршрут построен через API.",
            user_id=payload.user_id,
            route_id=route.route_id,
            payload=route.model_dump(mode="json"),
        )
    )
    return route.model_dump(mode="json")


@router.post("/api/scenario/start")
async def start_scenario(request: Request, payload: StartScenarioRequest) -> dict:
    scenario = request.app.state.scenario_engine.start(payload)
    return scenario.model_dump(mode="json")


@router.post("/api/scenario/stop")
async def stop_scenario(request: Request, payload: StopScenarioRequest) -> dict:
    scenario = request.app.state.scenario_engine.stop(payload.scenario_id)
    return scenario.model_dump(mode="json")


@router.post("/api/device/event")
async def post_device_event(request: Request, payload: DeviceEventEnvelope) -> dict:
    normalized = request.app.state.device_gateway.normalize_event(payload)
    return request.app.state.scenario_engine.handle_device_event(DeviceEventEnvelope.model_validate(normalized))


@router.get("/api/device/commands/{device_id}")
async def poll_device_commands(request: Request, device_id: str) -> dict:
    return {"device_id": device_id, "commands": request.app.state.device_gateway.poll_commands(device_id)}


@router.post("/api/device/heartbeat")
async def device_heartbeat(request: Request, payload: dict) -> dict:
    device_id = payload.get("device_id")
    if not device_id:
        raise HTTPException(status_code=400, detail="Поле device_id обязательно")
    request.app.state.device_gateway.register_heartbeat(device_id=device_id, payload=payload)
    return {"status": "ok"}


@router.post("/api/obstacle")
async def upsert_obstacle(request: Request, payload: ObstacleEvent) -> dict:
    request.app.state.obstacle_engine.upsert(payload)
    request.app.state.store.append_log(
        AuditRecord(
            event="obstacle_upserted",
            message=payload.message,
            payload=payload.model_dump(mode="json"),
        )
    )
    return {"status": "stored"}


@router.get("/api/eta")
async def get_eta(request: Request) -> ETAResponse:
    buses = list(request.app.state.store.buses.values())
    return ETAResponse(buses=buses, congestion_flags={bus.bus_id: bus.congestion for bus in buses})


@router.get("/api/recommendations")
async def get_recommendations(request: Request) -> RecommendationResponse:
    store = request.app.state.store
    return RecommendationResponse(weather=store.weather, congestion_warning=store.last_congestion_warning)


@router.get("/api/logs")
async def get_logs(request: Request, limit: int = 100) -> list[dict]:
    records = list(request.app.state.store.audit_log)[-limit:]
    return [record.model_dump(mode="json") for record in records]


@router.post("/api/voice/route-parse")
async def parse_voice_route(request: Request, payload: VoiceRouteParseRequest) -> dict:
    try:
        response = await request.app.state.deepseek_route_parser.parse_route_command(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:  # type: ignore[name-defined]
        raise HTTPException(status_code=502, detail=f"Ошибка связи с DeepSeek-R1: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Не удалось обработать ответ DeepSeek-R1: {exc}") from exc
    return response.model_dump(mode="json")


@router.post("/api/simulate/point-confirmation/{device_id}")
async def simulate_point_confirmation(request: Request, device_id: str, user_id: str) -> dict:
    return request.app.state.simulation.simulate_point_confirmation(device_id=device_id, user_id=user_id)


@router.post("/api/simulate/obstacle/{location_id}")
async def simulate_obstacle(request: Request, location_id: int, message: str = "Препятствие на маршруте") -> dict:
    return request.app.state.simulation.simulate_obstacle(location_id=location_id, message=message)


@router.post("/api/simulate/distance/{device_id}")
async def simulate_distance(request: Request, device_id: str, distance_cm: float) -> dict:
    return request.app.state.simulation.simulate_distance_sensor(device_id=device_id, distance_cm=distance_cm)
