from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import ValidationError

from app.models import (
    AnnounceRecommendationRequest,
    DistanceReading,
    EnvironmentReading,
    LightEvent,
    SoundEvent,
    StartNavigationRequest,
    SynthesizeSpeechRequest,
    VoiceEvent,
    city_event_adapter,
)


router = APIRouter(prefix="/api", tags=["api"])


async def _dispatch_event_chain(request: Request, initial_events: list[Any]) -> list[dict[str, Any]]:
    queue = list(initial_events)
    dispatched: list[dict[str, Any]] = []

    while queue:
        event = queue.pop(0)
        city_result = await request.app.state.city_client.send_event(
            event.model_dump(exclude_none=True)
        )
        followups = await request.app.state.local_state.register_forwarded_event(event, city_result)
        dispatched.append(
            {
                "accepted": city_result["ok"],
                "event": event.model_dump(exclude_none=True),
                "city": city_result,
            }
        )
        queue.extend(followups)

    return dispatched


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
async def get_raw_city_state(
    request: Request,
    snapshots: int = Query(default=5, ge=1, le=25),
) -> dict[str, Any]:
    return await request.app.state.local_state.raw_city_state(snapshot_limit=snapshots)


@router.get("/city/feed")
async def get_city_feed(
    request: Request,
    limit: int = Query(default=50, ge=1, le=400),
) -> dict[str, Any]:
    return await request.app.state.local_state.city_feed(limit=limit)


@router.post("/events")
async def post_event(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        event = city_event_adapter.validate_python(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    dispatched = await _dispatch_event_chain(request, [event])
    primary = dispatched[0]
    return {
        "accepted": primary["accepted"],
        "event": primary["event"],
        "city": primary["city"],
        "followups": dispatched[1:],
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
    dispatched = await _dispatch_event_chain(request, [event])
    return dispatched[0]


@router.post("/actions/default-light/{device_id}")
async def post_default_light(request: Request, device_id: int) -> dict[str, Any]:
    profile = request.app.state.team_profile
    signal = profile.get("signal", {}).get("type2", {})
    color = signal.get("color")
    if not color:
        raise HTTPException(status_code=400, detail="В team.json не задан preset для TYPE 3.")

    event = LightEvent(type=3, device_id=device_id, color=color)
    dispatched = await _dispatch_event_chain(request, [event])
    return dispatched[0]


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
    text_generation_service = getattr(request.app.state, "text_generation_service", None)
    if text_generation_service is not None:
        text = await asyncio.to_thread(
            text_generation_service.rewrite_text,
            text,
            intent="recommendation_announce",
            context={
                "kind": body.kind,
                "recommendation": recommendation,
            },
        )
    event = VoiceEvent(type=1, text=text)
    dispatched = await _dispatch_event_chain(request, [event])
    return dispatched[0]


@router.post("/tts")
async def synthesize_speech(
    request: Request,
    body: SynthesizeSpeechRequest,
) -> Response:
    tts_service = request.app.state.tts_service
    if not tts_service.is_ready():
        raise HTTPException(
            status_code=503,
            detail=tts_service.readiness_error() or "Нейросетевая озвучка недоступна.",
        )

    try:
        wav_bytes = await asyncio.to_thread(tts_service.synthesize_bytes, body.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ModuleNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={
            "Cache-Control": "public, max-age=31536000",
            "X-TTS-Engine": "piper",
            "X-TTS-Voice": tts_service.voice_name,
        },
    )


@router.post("/navigation/start")
async def start_navigation(
    request: Request,
    body: StartNavigationRequest,
) -> dict[str, Any]:
    try:
        initial_events = await request.app.state.local_state.start_navigation(
            destination_point_id=body.destination_point_id,
            start_point_id=body.start_point_id,
            waypoint_point_ids=body.waypoint_point_ids,
            service_id=body.service_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    dispatched = await _dispatch_event_chain(request, initial_events)
    state = await request.app.state.local_state.snapshot()
    return {
        "ok": True,
        "navigation": state["navigation"],
        "dispatched": dispatched,
    }


@router.get("/devices/{device_id}/command")
async def get_device_command(
    request: Request,
    device_id: int,
    device_type: str = Query(..., pattern="^(type1|type2|vibration)$"),
) -> dict[str, Any]:
    return await request.app.state.local_state.get_device_command(device_id, device_type)
