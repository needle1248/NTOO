from __future__ import annotations

import asyncio
from html import escape
from typing import Any

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError

from app.models import (
    AnnounceRecommendationRequest,
    BoardHeartbeat,
    CameraPullRequest,
    DistanceReading,
    EnvironmentReading,
    FaceEvent,
    LightEvent,
    SoundEvent,
    StartNavigationRequest,
    SynthesizeSpeechRequest,
    VoiceEvent,
    city_event_adapter,
)


router = APIRouter(prefix="/api", tags=["api"])
compat_router = APIRouter(tags=["compat"])


def _face_runtime(request: Request):
    runtime = getattr(request.app.state, "face_runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="Распознавание лиц не подключено.")
    return runtime


def _pending_city_result() -> dict[str, Any]:
    return {
        "ok": True,
        "status_code": None,
        "response_text": "accepted locally; city forward pending",
        "latency_ms": 0.0,
        "local_first": True,
        "pending": True,
    }


def _normalize_city_result(result: dict[str, Any] | BaseException) -> dict[str, Any]:
    if isinstance(result, BaseException):
        return {
            "ok": False,
            "status_code": None,
            "response_text": str(result),
            "latency_ms": 0.0,
        }
    return result


async def _forward_events_to_city_and_record(
    local_state: Any,
    city_client: Any,
    event_payloads: list[dict[str, Any]],
) -> None:
    city_results = await asyncio.gather(
        *(city_client.send_event(payload) for payload in event_payloads),
        return_exceptions=True,
    )
    for raw_result in city_results:
        await local_state.record_forward_result(_normalize_city_result(raw_result))


def _schedule_background_city_forward(
    request: Request,
    event_payloads: list[dict[str, Any]],
) -> None:
    if not event_payloads:
        return

    registry = getattr(request.app.state, "background_tasks", None)
    if registry is None:
        registry = set()
        request.app.state.background_tasks = registry

    task = asyncio.create_task(
        _forward_events_to_city_and_record(
            request.app.state.local_state,
            request.app.state.city_client,
            list(event_payloads),
        )
    )
    registry.add(task)

    def _cleanup(finished: asyncio.Task[Any]) -> None:
        registry.discard(finished)
        try:
            finished.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    task.add_done_callback(_cleanup)


async def _run_face_job(function: Any, *args: Any) -> Any:
    try:
        return await asyncio.to_thread(function, *args)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


async def _read_image_payload(
    request: Request,
    image: UploadFile | None,
) -> bytes:
    if image is not None:
        return await image.read()
    return await request.body()


async def _dispatch_event_payload(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
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


async def _dispatch_event_chain(
    request: Request,
    initial_events: list[Any],
    *,
    await_city: bool = True,
) -> list[dict[str, Any]]:
    queue = list(initial_events)
    dispatched: list[dict[str, Any]] = []
    city_task_indexes: list[int] = []
    event_payloads: list[dict[str, Any]] = []

    while queue:
        event = queue.pop(0)
        event_payload = event.model_dump(exclude_none=True)
        local_result = _pending_city_result()
        followups = await request.app.state.local_state.register_forwarded_event(
            event,
            local_result,
            count_metrics=False,
        )
        dispatched.append(
            {
                "accepted": True,
                "event": event_payload,
                "city": local_result,
            }
        )
        event_payloads.append(event_payload)
        city_task_indexes.append(len(dispatched) - 1)
        queue.extend(followups)

    if event_payloads and await_city:
        city_results = await asyncio.gather(
            *(request.app.state.city_client.send_event(payload) for payload in event_payloads),
            return_exceptions=True,
        )
        for dispatched_index, raw_result in zip(city_task_indexes, city_results, strict=True):
            city_result = _normalize_city_result(raw_result)
            await request.app.state.local_state.record_forward_result(city_result)
            dispatched[dispatched_index]["accepted"] = city_result["ok"]
            dispatched[dispatched_index]["city"] = city_result
    elif event_payloads:
        _schedule_background_city_forward(request, event_payloads)

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


@router.get("/faces/status")
async def get_face_status(request: Request) -> dict[str, Any]:
    return _face_runtime(request).get_status()


@router.post("/faces/train")
async def train_faces(
    request: Request,
    user_id: str = Form(...),
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    runtime = _face_runtime(request)
    images: list[tuple[str, bytes]] = []
    for file in files:
        images.append((file.filename or "face.jpg", await file.read()))
    return await _run_face_job(runtime.train_faces_from_uploads, user_id, images)


@router.post("/faces/retrain")
async def retrain_faces(request: Request) -> dict[str, Any]:
    return await _run_face_job(_face_runtime(request).retrain_faces)


async def _recognize_face_and_dispatch(
    request: Request,
    device_id: int,
    image_bytes: bytes,
) -> dict[str, Any]:
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Пустой кадр камеры.")

    result = await _run_face_job(
        _face_runtime(request).recognize_face_bytes,
        device_id,
        image_bytes,
    )
    prediction = result["prediction"]
    result["dispatched"] = []

    if prediction.get("matched") and prediction.get("user_id"):
        event = FaceEvent(
            type=6,
            device_id=device_id,
            user_id=prediction["user_id"],
            confidence=float(prediction.get("confidence") or 0.0),
        )
        result["dispatched"] = await _dispatch_event_chain(
            request,
            [event],
            await_city=False,
        )

    return result


@router.post("/faces/recognize/{device_id}")
async def recognize_face(
    request: Request,
    device_id: int,
    image: UploadFile = File(...),
) -> dict[str, Any]:
    return await _recognize_face_and_dispatch(request, device_id, await image.read())


@router.get("/camera-log")
async def get_camera_log(
    request: Request,
    limit: int = Query(default=25, ge=1, le=100),
) -> list[dict[str, Any]]:
    return [
        snapshot.model_dump(mode="json")
        for snapshot in _face_runtime(request).get_recent_snapshots(limit=limit)
    ]


@router.get("/device-status")
async def get_device_status(request: Request) -> list[dict[str, Any]]:
    return await request.app.state.local_state.board_status()


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


def _compat_command_payload(
    device_id: int,
    command_type: str,
    command_state: dict[str, Any],
) -> dict[str, Any]:
    command = command_state.get("command") or {}
    active = bool(command_state.get("active"))
    payload = dict(command) if isinstance(command, dict) else {}

    if command_type == "type1_sound":
        payload = {
            key: value
            for key, value in {
                "frequency_hz": command.get("frequency_hz"),
                "duration_ms": command.get("duration_ms"),
                "melody": command.get("melody"),
            }.items()
            if value is not None
        }
    elif command_type == "type2_light":
        payload = {"color": command.get("color")} if command.get("color") is not None else {}

    return {
        "device_id": device_id,
        "active": active,
        "command_type": command_type,
        "payload": payload,
        "reason": command.get("reason") or ("current_state" if active else "idle"),
        "updated_at": command.get("updated_at") or command.get("timestamp"),
        "expires_at": command.get("expires_at"),
    }


@compat_router.post("/event")
async def post_compat_event(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    return await _dispatch_event_payload(request, payload)


@compat_router.get("/health")
async def get_compat_health(request: Request) -> dict[str, Any]:
    return await health(request)


@compat_router.get("/state")
async def get_compat_state(request: Request) -> dict[str, Any]:
    return await request.app.state.local_state.snapshot()


@compat_router.get("/debug/state")
async def get_compat_debug_state(request: Request) -> dict[str, Any]:
    return await request.app.state.local_state.snapshot()


@compat_router.post("/voice")
async def post_compat_voice(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    return await _dispatch_event_payload(request, {"type": 1, **payload})


@compat_router.post("/events/rfid")
async def post_compat_rfid(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    return await _dispatch_event_payload(request, {"type": 4, **payload})


@compat_router.post("/events/face")
async def post_compat_face_event(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    return await _dispatch_event_payload(request, {"type": 6, **payload})


@compat_router.post("/events/obstacle")
async def post_compat_obstacle(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    return await _dispatch_event_payload(request, {"type": 5, **payload})


@compat_router.post("/events/environment")
async def post_compat_environment(
    request: Request,
    payload: dict[str, Any],
) -> dict[str, Any]:
    payload.pop("type", None)
    if "humidity_pct" in payload and "humidity_percent" not in payload:
        payload["humidity_percent"] = payload.pop("humidity_pct")
    reading = EnvironmentReading.model_validate(payload)
    await request.app.state.local_state.update_environment(reading)
    state = await request.app.state.local_state.snapshot()
    return {
        "ok": True,
        "environment": state["environment"],
        "recommendation": state["recommendations"]["clothing"],
    }


@compat_router.post("/events/distance")
async def post_compat_distance(
    request: Request,
    payload: dict[str, Any],
) -> dict[str, Any]:
    payload.pop("type", None)
    if "detected" in payload and "bus_detected" not in payload:
        payload["bus_detected"] = payload.pop("detected")
    reading = DistanceReading.model_validate(payload)
    await request.app.state.local_state.update_distance(reading)
    state = await request.app.state.local_state.snapshot()
    return {
        "ok": True,
        "distance": state["distance"],
        "vibration": state["vibration"],
    }


@compat_router.get("/devices/type1/{device_id}/command")
async def get_compat_type1_command(request: Request, device_id: int) -> dict[str, Any]:
    command = await request.app.state.local_state.get_device_command(device_id, "type1")
    return _compat_command_payload(device_id, "type1_sound", command)


@compat_router.post("/devices/type1/{device_id}/play-test")
async def post_compat_type1_play_test(request: Request, device_id: int) -> dict[str, Any]:
    signal = request.app.state.team_profile.get("signal", {}).get("type1", {})
    event = SoundEvent(
        type=2,
        device_id=device_id,
        duration_ms=int(signal.get("duration_ms") or 1000),
        frequency_hz=int(signal.get("frequency_hz") or 1000),
    )
    await _dispatch_event_chain(request, [event])
    command = await request.app.state.local_state.get_device_command(device_id, "type1")
    return _compat_command_payload(device_id, "type1_sound", command)


@compat_router.get("/devices/type2/{device_id}/command")
async def get_compat_type2_command(request: Request, device_id: int) -> dict[str, Any]:
    command = await request.app.state.local_state.get_device_command(device_id, "type2")
    return _compat_command_payload(device_id, "type2_light", command)


@compat_router.get("/devices/vibro/{device_id}/command")
async def get_compat_vibro_command(request: Request, device_id: int) -> dict[str, Any]:
    command = await request.app.state.local_state.get_device_command(device_id, "vibration")
    return _compat_command_payload(device_id, "vibro", command)


@compat_router.post("/devices/heartbeat")
async def post_device_heartbeat(
    request: Request,
    heartbeat: BoardHeartbeat,
) -> dict[str, Any]:
    return await request.app.state.local_state.register_board_heartbeat(heartbeat)


@compat_router.get("/faces/status")
async def get_compat_face_status(request: Request) -> dict[str, Any]:
    return await get_face_status(request)


@compat_router.get("/device-status", response_class=HTMLResponse)
async def get_device_status_page(request: Request) -> HTMLResponse:
    boards = await request.app.state.local_state.board_status()
    rows = "".join(
        f"""
        <tr>
          <td>{escape(str(board.get("board_key", "-")))}</td>
          <td>{escape(str(board.get("firmware") or "-"))}</td>
          <td>{escape(str(board.get("ip_address") or "-"))}</td>
          <td>{'online' if board.get("online") else 'offline'}</td>
          <td>{escape(str(board.get("seconds_since_seen", "-")))}</td>
        </tr>
        """
        for board in boards
    ) or "<tr><td colspan='5'>No board heartbeats yet.</td></tr>"
    return HTMLResponse(
        f"""
        <!doctype html>
        <html lang="ru">
        <head>
          <meta charset="utf-8" />
          <meta name="viewport" content="width=device-width, initial-scale=1" />
          <title>Board Status</title>
          <style>
            body {{ font-family: Segoe UI, sans-serif; margin: 24px; background: #f6f8fa; color: #17212b; }}
            table {{ border-collapse: collapse; width: 100%; background: white; }}
            th, td {{ border: 1px solid #d9e2ec; padding: 10px 12px; text-align: left; }}
            th {{ background: #eef7f7; }}
          </style>
        </head>
        <body>
          <h1>Board Connection Status</h1>
          <table>
            <thead>
              <tr><th>Board</th><th>Firmware</th><th>IP</th><th>Status</th><th>Seconds since seen</th></tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </body>
        </html>
        """
    )


@compat_router.get("/camera-log", response_class=HTMLResponse)
async def get_camera_log_page(request: Request) -> HTMLResponse:
    snapshots = _face_runtime(request).get_recent_snapshots(limit=25)
    cards = "".join(
        f"""
        <article>
          <img src="{escape(snapshot.image_url)}" alt="camera frame" />
          <p>device={snapshot.device_id}, matched={snapshot.matched}, user={escape(str(snapshot.user_id or "-"))}</p>
        </article>
        """
        for snapshot in snapshots
    ) or "<p>No camera frames yet.</p>"
    return HTMLResponse(
        f"""
        <!doctype html>
        <html lang="ru">
        <head>
          <meta charset="utf-8" />
          <meta name="viewport" content="width=device-width, initial-scale=1" />
          <title>Camera Log</title>
          <style>
            body {{ font-family: Segoe UI, sans-serif; margin: 24px; background: #f6f8fa; color: #17212b; }}
            main {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; }}
            article {{ padding: 12px; border: 1px solid #d9e2ec; border-radius: 16px; background: white; }}
            img {{ width: 100%; border-radius: 12px; display: block; }}
          </style>
        </head>
        <body>
          <h1>Camera Log</h1>
          <main>{cards}</main>
        </body>
        </html>
        """
    )


@compat_router.post("/faces/train")
async def train_compat_faces(
    request: Request,
    user_id: str = Form(...),
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    return await train_faces(request, user_id=user_id, files=files)


@compat_router.post("/faces/retrain")
async def retrain_compat_faces(request: Request) -> dict[str, Any]:
    return await retrain_faces(request)


@compat_router.post("/faces/recognize/{device_id}")
async def recognize_compat_face(
    request: Request,
    device_id: int,
    image: UploadFile = File(...),
) -> dict[str, Any]:
    return await _recognize_face_and_dispatch(request, device_id, await image.read())


@compat_router.post("/devices/esp32-cam/{device_id}/frame")
async def post_esp32_cam_frame(
    request: Request,
    device_id: int,
    image: UploadFile | None = File(default=None),
) -> dict[str, Any]:
    image_bytes = await _read_image_payload(request, image)
    return await _recognize_face_and_dispatch(request, device_id, image_bytes)


@compat_router.post("/devices/esp32-cam/{device_id}/enroll")
async def post_esp32_cam_enroll(
    request: Request,
    device_id: int,
    user_id: str = Query(..., min_length=1),
    retrain: bool = Query(default=False),
    image: UploadFile | None = File(default=None),
) -> dict[str, Any]:
    image_bytes = await _read_image_payload(request, image)
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Пустой кадр камеры.")
    return await _run_face_job(
        _face_runtime(request).enroll_face_bytes,
        user_id,
        device_id,
        image_bytes,
        retrain,
    )


@compat_router.post("/devices/esp32-cam/{device_id}/pull")
async def post_esp32_cam_pull(
    request: Request,
    device_id: int,
    payload: CameraPullRequest,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(payload.camera_url)
        response.raise_for_status()
    return await _recognize_face_and_dispatch(request, device_id, response.content)
