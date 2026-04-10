from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings, load_reference_data, load_team_profile
from app.routers.api import compat_router, router as api_router
from app.services.city_client import CityClient
from app.services.face_runtime import FaceRuntimeService
from app.services.local_state import LocalState
from app.services.text_generation_service import TextGenerationService
from app.services.tts_service import NeuralTtsService


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


async def city_poll_loop(app: FastAPI) -> None:
    interval = app.state.settings.city_poll_interval_seconds
    while True:
        try:
            payload = await app.state.city_client.fetch_debug_state()
            await app.state.local_state.refresh_city_state(payload)
        except Exception as exc:  # noqa: BLE001
            await app.state.local_state.mark_city_error(str(exc))
        await asyncio.sleep(interval)


def create_app() -> FastAPI:
    settings = get_settings()
    reference_data = load_reference_data(settings)
    team_profile = load_team_profile(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.team_profile = team_profile
        app.state.city_client = CityClient(settings)
        app.state.text_generation_service = TextGenerationService(settings)
        app.state.face_runtime = FaceRuntimeService(settings)
        app.state.face_runtime.startup()
        app.state.local_state = LocalState(
            team_profile,
            signal_catalog=reference_data.get("signal_presets", {}),
            city_receive_log_path=settings.city_receive_log_path,
            city_receive_log_entries_limit=settings.city_receive_log_entries_limit,
            city_receive_log_updates_preview_limit=settings.city_receive_log_updates_preview_limit,
            board_offline_after_seconds=settings.board_offline_after_seconds,
            text_generation_service=app.state.text_generation_service,
        )
        app.state.tts_service = NeuralTtsService(settings)

        poll_task = None
        if settings.enable_city_polling:
            poll_task = asyncio.create_task(city_poll_loop(app))

        try:
            yield
        finally:
            if poll_task is not None:
                poll_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await poll_task
            await app.state.city_client.close()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    settings.snapshot_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/camera-log/files",
        StaticFiles(directory=str(settings.snapshot_dir)),
        name="camera-log-files",
    )
    app.include_router(api_router)
    app.include_router(compat_router)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "request": request,
                "team": team_profile,
                "settings": settings,
            },
        )

    return app
