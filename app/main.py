from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings, load_team_profile
from app.routers.api import router as api_router
from app.services.city_client import CityClient
from app.services.local_state import LocalState


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
    team_profile = load_team_profile(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.team_profile = team_profile
        app.state.city_client = CityClient(settings)
        app.state.local_state = LocalState(team_profile)

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
    app.include_router(api_router)

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
