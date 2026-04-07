import logging
from pathlib import Path

from digital_guide.core.models import (
    CityConfig,
    ConfirmationMode,
    CongestionConfig,
    DeviceEventEnvelope,
    EdgeType,
    RecommendationConfig,
    RouteEdge,
    RouteGraph,
    RouteNode,
    SimulationConfig,
    StartScenarioRequest,
    TeamConfig,
)
from digital_guide.core.state import AppStateStore
from digital_guide.services.city_event_sender import CityEventSender
from digital_guide.services.congestion_engine import CongestionEngine
from digital_guide.services.device_gateway import DeviceGateway
from digital_guide.services.eta_engine import ETAEngine
from digital_guide.services.obstacle_engine import ObstacleEngine
from digital_guide.services.persistence import JsonPersistence
from digital_guide.services.recommendation_engine import RecommendationEngine
from digital_guide.services.route_engine import RouteEngine
from digital_guide.services.scenario_engine import ScenarioEngine
from digital_guide.services.session_manager import SessionManager
from digital_guide.services.voice_engine import VoiceEngine


def build_graph() -> RouteGraph:
    return RouteGraph(
        stop_mappings={"left_ring": [9, 10, 13, 16]},
        indoor_mappings={},
        nodes=[
            RouteNode(node_id=1, label="Start", node_type="nav_point", ring_id="left_ring", command_id=1, device_id="d1"),
            RouteNode(node_id=2, label="Mid", node_type="nav_point", ring_id="left_ring", command_id=2, device_id="d2"),
            RouteNode(node_id=3, label="Alt", node_type="nav_point", ring_id="left_ring", command_id=3, device_id="d3"),
            RouteNode(node_id=4, label="Goal", node_type="nav_point", ring_id="left_ring", command_id=4, device_id="d4"),
        ],
        edges=[
            RouteEdge(from_node=1, to_node=2, edge_type=EdgeType.PEDESTRIAN, weight=1),
            RouteEdge(from_node=2, to_node=4, edge_type=EdgeType.PEDESTRIAN, weight=1),
            RouteEdge(from_node=1, to_node=3, edge_type=EdgeType.PEDESTRIAN, weight=1),
            RouteEdge(from_node=3, to_node=4, edge_type=EdgeType.PEDESTRIAN, weight=3),
        ],
    )


def build_team_config() -> TeamConfig:
    return TeamConfig(
        team_id="team-01",
        team_name="Demo",
        primary_ring="left_ring",
        user_id="user_demo",
        selected_confirmation_mode=ConfirmationMode.FACE,
        face_threshold=0.85,
        primary_signal_profile="default_type1",
        city=CityConfig(base_url="http://localhost:8000", state_url="http://localhost:8000/debug/state", access_token="token"),
        congestion=CongestionConfig(),
        recommendation=RecommendationConfig(),
        simulation=SimulationConfig(enabled=False),
        ring_stop_order={"left_ring": [9, 10, 13, 16]},
    )


def build_engine(tmp_path: Path) -> tuple[ScenarioEngine, AppStateStore]:
    logger = logging.getLogger(f"scenario-test-{tmp_path.name}")
    logger.handlers.clear()
    logger.setLevel(logging.CRITICAL)
    store = AppStateStore(
        team_config=build_team_config(),
        logger=logger,
        persistence=JsonPersistence(tmp_path),
    )
    route_engine = RouteEngine(build_graph())
    eta_engine = ETAEngine(build_graph())
    obstacle_engine = ObstacleEngine(store)
    device_gateway = DeviceGateway(store, logger)
    city_event_sender = CityEventSender(store, logger, store.team_config.city)
    voice_engine = VoiceEngine(city_event_sender, logger)
    session_manager = SessionManager(store, logger)
    engine = ScenarioEngine(
        store=store,
        route_engine=route_engine,
        obstacle_engine=obstacle_engine,
        recommendation_engine=RecommendationEngine(store.team_config.recommendation),
        eta_engine=eta_engine,
        congestion_engine=CongestionEngine(store.team_config.congestion),
        device_gateway=device_gateway,
        voice_engine=voice_engine,
        city_event_sender=city_event_sender,
        session_manager=session_manager,
    )
    return engine, store


def test_scenario_advances_on_face_confirmation(tmp_path: Path) -> None:
    engine, store = build_engine(tmp_path)
    scenario = engine.start(StartScenarioRequest(scenario_kind="walk", user_id="user_demo", start_node=1, goal_node=4))
    assert scenario.status.value == "waiting_confirmation"
    assert store.device_command_queue["d2"][0]["command_id"] == 2

    response = engine.handle_device_event(DeviceEventEnvelope(type=6, device_id="d2", user_id="user_demo", confidence=0.93))
    assert response["status"] == "advanced"
    assert store.pending_city_events[0]["type"] == 6


def test_scenario_reroutes_on_obstacle(tmp_path: Path) -> None:
    engine, store = build_engine(tmp_path)
    engine.start(StartScenarioRequest(scenario_kind="walk", user_id="user_demo", start_node=1, goal_node=4))
    response = engine.handle_device_event(
        DeviceEventEnvelope(type=5, location_id=2, obstacle_type="barrier", reroute_required=True, message="Blocked point")
    )
    assert response["status"] == "rerouted"
    assert [node.node_id for node in store.active_route.nodes] == [1, 3, 4]
