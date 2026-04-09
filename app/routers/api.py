from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError

from app.models import (
    AnnounceRecommendationRequest,
    DistanceReading,
    EnvironmentReading,
    LightEvent,
    SoundEvent,
    VoiceEvent,
    city_event_adapter,
)


router = APIRouter(prefix="/api", tags=["api"])


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    state = await request.app.state.local_state.snapshot()
    return {
        "ok": True,
        "city_connected": state["city"]["connected"],
        "forwarded_ok": state["metrics"]["forwarded_ok"],
        "forwarded_failed": state["metrics"]["forwarded_failed"],
    }


@router.get("/state")
async def get_state(request: Request) -> dict[str, Any]:
    return await request.app.state.local_state.snapshot()


@router.get("/city/raw")
async def get_raw_city_state(request: Request) -> dict[str, Any]:
    raw = await request.app.state.local_state.raw_city_state()
    return {"raw": raw}


@router.post("/events")
async def post_event(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        event = city_event_adapter.validate_python(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    city_result = await request.app.state.city_client.send_event(event.model_dump())
    await request.app.state.local_state.register_forwarded_event(event, city_result)
    return {
        "accepted": city_result["ok"],
        "event": event.model_dump(),
        "city": city_result,
    }


@router.post("/sensors/environment")
async def post_environment(request: Request, reading: EnvironmentReading) -> dict[str, Any]:
    await request.app.state.local_state.update_environment(reading)
    state = await request.app.state.local_state.snapshot()
    return {
        "ok": True,
        "environment": state["environment"],
        "recommendation": state["recommendations"]["clothing"],
    }


@router.post("/sensors/distance")
async def post_distance(request: Request, reading: DistanceReading) -> dict[str, Any]:
    await request.app.state.local_state.update_distance(reading)
    state = await request.app.state.local_state.snapshot()
    return {
        "ok": True,
        "distance": state["distance"],
        "vibration": state["vibration"],
    }


@router.post("/actions/default-sound/{device_id}")
async def post_default_sound(request: Request, device_id: int) -> dict[str, Any]:
    profile = request.app.state.team_profile
    signal = profile.get("signal", {}).get("type1", {})
    if not signal:
        raise HTTPException(status_code=400, detail="В team.json не задан preset для TYPE 2.")

    event = SoundEvent(
        type=2,
        device_id=device_id,
        duration_ms=int(signal["duration_ms"]),
        frequency_hz=int(signal["frequency_hz"]),
    )
    city_result = await request.app.state.city_client.send_event(event.model_dump())
    await request.app.state.local_state.register_forwarded_event(event, city_result)
    return {"accepted": city_result["ok"], "event": event.model_dump(), "city": city_result}


@router.post("/actions/default-light/{device_id}")
async def post_default_light(request: Request, device_id: int) -> dict[str, Any]:
    profile = request.app.state.team_profile
    signal = profile.get("signal", {}).get("type2", {})
    color = signal.get("color")
    if not color:
        raise HTTPException(status_code=400, detail="В team.json не задан preset для TYPE 3.")

    event = LightEvent(type=3, device_id=device_id, color=color)
    city_result = await request.app.state.city_client.send_event(event.model_dump())
    await request.app.state.local_state.register_forwarded_event(event, city_result)
    return {"accepted": city_result["ok"], "event": event.model_dump(), "city": city_result}


@router.post("/actions/announce-recommendation")
async def announce_recommendation(
    request: Request,
    body: AnnounceRecommendationRequest,
) -> dict[str, Any]:
    state = await request.app.state.local_state.snapshot()
    recommendation = state["recommendations"].get(body.kind)
    if not recommendation:
        raise HTTPException(status_code=400, detail="Нет данных для озвучивания.")

    text = recommendation["text"]
    event = VoiceEvent(type=1, text=text)
    city_result = await request.app.state.city_client.send_event(event.model_dump())
    await request.app.state.local_state.register_forwarded_event(event, city_result)
    return {"accepted": city_result["ok"], "event": event.model_dump(), "city": city_result}


@router.get("/devices/{device_id}/command")
async def get_device_command(
    request: Request,
    device_id: int,
    device_type: str = Query(..., pattern="^(type1|type2|vibration)$"),
) -> dict[str, Any]:
    return await request.app.state.local_state.get_device_command(device_id, device_type)
