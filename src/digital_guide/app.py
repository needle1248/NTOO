from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from digital_guide.api.routes import router
from digital_guide.core.config import load_runtime
from digital_guide.core.logging import configure_logging
from digital_guide.core.state import AppStateStore
from digital_guide.services.city_event_sender import CityEventSender
from digital_guide.services.city_state_poller import CityStatePoller
from digital_guide.services.congestion_engine import CongestionEngine
from digital_guide.services.deepseek_route_parser import DeepSeekRouteParser
from digital_guide.services.device_gateway import DeviceGateway
from digital_guide.services.eta_engine import ETAEngine
from digital_guide.services.obstacle_engine import ObstacleEngine
from digital_guide.services.persistence import JsonPersistence
from digital_guide.services.recommendation_engine import RecommendationEngine
from digital_guide.services.route_engine import RouteEngine
from digital_guide.services.scenario_engine import ScenarioEngine
from digital_guide.services.session_manager import SessionManager
from digital_guide.services.simulation import SimulationService
from digital_guide.services.voice_engine import VoiceEngine


def create_app() -> FastAPI:
    config_dir = Path(__file__).resolve().parents[2] / "configs"
    runtime = load_runtime(config_dir)
    logger = configure_logging(runtime.team_config)
    persistence = JsonPersistence(base_dir=Path(__file__).resolve().parents[2] / "data")
    store = AppStateStore(team_config=runtime.team_config, logger=logger, persistence=persistence)
    store.devices = {device.device_id: device.model_dump(mode="json") for device in runtime.route_graph.devices}
    route_engine = RouteEngine(runtime.route_graph)
    eta_engine = ETAEngine(runtime.route_graph)
    congestion_engine = CongestionEngine(runtime.team_config.congestion)
    obstacle_engine = ObstacleEngine(store=store)
    recommendation_engine = RecommendationEngine(runtime.team_config.recommendation)
    deepseek_route_parser = DeepSeekRouteParser(config=runtime.team_config.deepseek, logger=logger)
    device_gateway = DeviceGateway(store=store, logger=logger)
    city_event_sender = CityEventSender(store=store, logger=logger, city_config=runtime.team_config.city)
    voice_engine = VoiceEngine(city_event_sender=city_event_sender, logger=logger)
    session_manager = SessionManager(store=store, logger=logger)
    scenario_engine = ScenarioEngine(
        store=store,
        route_engine=route_engine,
        obstacle_engine=obstacle_engine,
        recommendation_engine=recommendation_engine,
        eta_engine=eta_engine,
        congestion_engine=congestion_engine,
        device_gateway=device_gateway,
        voice_engine=voice_engine,
        city_event_sender=city_event_sender,
        session_manager=session_manager,
    )
    city_poller = CityStatePoller(
        store=store,
        logger=logger,
        city_config=runtime.team_config.city,
        eta_engine=eta_engine,
        congestion_engine=congestion_engine,
        recommendation_engine=recommendation_engine,
    )
    simulation = SimulationService(store=store, logger=logger, scenario_engine=scenario_engine, eta_engine=eta_engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime = runtime
        app.state.logger = logger
        app.state.store = store
        app.state.route_engine = route_engine
        app.state.eta_engine = eta_engine
        app.state.congestion_engine = congestion_engine
        app.state.obstacle_engine = obstacle_engine
        app.state.recommendation_engine = recommendation_engine
        app.state.deepseek_route_parser = deepseek_route_parser
        app.state.device_gateway = device_gateway
        app.state.city_event_sender = city_event_sender
        app.state.voice_engine = voice_engine
        app.state.session_manager = session_manager
        app.state.scenario_engine = scenario_engine
        app.state.city_poller = city_poller
        app.state.simulation = simulation

        tasks = []
        if runtime.team_config.city.enabled:
            tasks.extend(
                [
                    asyncio.create_task(city_event_sender.run(), name="city-event-sender"),
                    asyncio.create_task(city_poller.run(), name="city-state-poller"),
                ]
            )
        if runtime.team_config.simulation.enabled:
            tasks.append(asyncio.create_task(simulation.run(), name="simulation"))

        yield

        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        store.flush()

    app = FastAPI(title="Цифровой поводырь", version="0.1.0", lifespan=lifespan)
    app.title = f"{runtime.team_config.team_name} | Digital Guide"
    app.include_router(router)
    web_dir = Path(__file__).resolve().parent / "web"
    app.mount("/static", StaticFiles(directory=str(web_dir / "static")), name="static")
    return app
