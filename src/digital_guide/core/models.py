from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ConfirmationMode(str, Enum):
    RFID = "rfid"
    FACE = "face"


class ScenarioKind(str, Enum):
    WALK = "walk"
    BUS = "bus"
    INDOOR = "indoor"
    MIXED = "mixed"
    TRANSPORT = "transport"
    MFC = "mfc"
    ETA = "eta"
    RECOMMENDATION = "recommendation"
    VIBRO = "vibro"


class ScenarioStatus(str, Enum):
    IDLE = "idle"
    PLANNED = "planned"
    RUNNING = "running"
    WAITING_CONFIRMATION = "waiting_confirmation"
    WAITING_BUS = "waiting_bus"
    COMPLETED = "completed"
    ERROR = "error"
    STOPPED = "stopped"


class DeviceKind(str, Enum):
    TYPE1 = "nav_point_type1"
    TYPE2 = "nav_point_type2"
    VIBRO = "vibro_platform"
    WEATHER = "weather_sensor"
    CAMERA = "camera"
    RFID = "rfid_reader"


class EdgeType(str, Enum):
    PEDESTRIAN = "pedestrian"
    BUS = "bus"
    INDOOR = "indoor"
    TRANSFER = "transfer"


class RouteActionType(str, Enum):
    GO_TO_POINT = "go_to_point"
    WAIT_CONFIRMATION = "wait_confirmation"
    BOARD_BUS = "board_bus"
    EXIT_BUS = "exit_bus"
    SWITCH_RING = "switch_ring"
    ENTER_MFC = "enter_mfc"
    GO_TO_SERVICE_WINDOW = "go_to_service_window"


class WeatherMode(str, Enum):
    COLD = "cold"
    NORMAL = "normal"
    HOT = "hot"


class SignalCommand(BaseModel):
    command_id: int
    duration_ms: int
    frequency_hz: int | None = None
    rgb: dict[str, int] | None = None
    label: str


class SignalProfile(BaseModel):
    profile_id: str
    device_kind: Literal["type1", "type2"]
    commands: list[SignalCommand]


class DeviceDescriptor(BaseModel):
    device_id: str
    logical_role: str
    device_kind: DeviceKind
    location_id: int | None = None
    active: bool = True
    ip_address: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeviceHeartbeat(BaseModel):
    device_id: str
    device_kind: DeviceKind
    timestamp: datetime = Field(default_factory=utc_now)
    status: str = "ok"
    active: bool = True
    last_seen: datetime = Field(default_factory=utc_now)
    firmware_version: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class DeviceType1State(BaseModel):
    device_id: str
    location_id: int
    timestamp: datetime = Field(default_factory=utc_now)
    active: bool = True
    last_seen: datetime = Field(default_factory=utc_now)
    status: str = "idle"
    expected_user_id: str | None = None
    confirmation_mode: ConfirmationMode = ConfirmationMode.RFID
    confirmation_status: str = "pending"
    signal_profile_id: str = "default_type1"
    command_id: int | None = None
    frequency_hz: int | None = None
    duration_ms: int | None = None
    heartbeat: DeviceHeartbeat | None = None


class DeviceType2State(BaseModel):
    device_id: str
    location_id: int
    timestamp: datetime = Field(default_factory=utc_now)
    active: bool = True
    last_seen: datetime = Field(default_factory=utc_now)
    status: str = "idle"
    expected_user_id: str | None = None
    confirmation_mode: ConfirmationMode = ConfirmationMode.RFID
    confirmation_status: str = "pending"
    signal_profile_id: str = "default_type2"
    command_id: int | None = None
    rgb: dict[str, int] | None = None
    duration_ms: int | None = None
    heartbeat: DeviceHeartbeat | None = None


class BusState(BaseModel):
    bus_id: str
    ring_id: str
    current_stop: int
    timestamp: datetime
    last_seen: datetime = Field(default_factory=utc_now)
    active: bool = True
    lap_time_seconds: float | None = None
    rolling_lap_average_seconds: float | None = None
    eta_by_stop: dict[int, float] = Field(default_factory=dict)
    congestion: bool = False


class RFIDEvent(BaseModel):
    type: Literal[4] = 4
    device_id: str
    location_id: int | None = None
    user_id: str | None = None
    rfid_code: str
    timestamp: datetime = Field(default_factory=utc_now)


class FaceEvent(BaseModel):
    type: Literal[6] = 6
    device_id: str
    user_id: str
    expected_user_id: str | None = None
    confidence: float
    timestamp: datetime = Field(default_factory=utc_now)


class ObstacleEvent(BaseModel):
    type: Literal[5] = 5
    location_id: int
    obstacle_type: str
    reroute_required: bool = True
    message: str
    active: bool = True
    timestamp: datetime = Field(default_factory=utc_now)


class VoiceMessage(BaseModel):
    type: Literal[1] = 1
    text: str
    target_user_id: str | None = None
    scenario_id: str | None = None
    timestamp: datetime = Field(default_factory=utc_now)
    variants: list[str] = Field(default_factory=list)


class UserProfile(BaseModel):
    user_id: str
    display_name: str
    team_id: str
    home_ring: str
    confirmation_mode: ConfirmationMode
    rfid_code: str | None = None
    face_embedding_id: str | None = None
    accessibility: dict[str, Any] = Field(default_factory=dict)


class RouteNode(BaseModel):
    node_id: int
    label: str
    node_type: str
    ring_id: str
    command_id: int | None = None
    device_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RouteEdge(BaseModel):
    from_node: int
    to_node: int
    edge_type: EdgeType
    weight: float = 1.0
    bidirectional: bool = True


class RouteSegment(BaseModel):
    from_node: int
    to_node: int
    edge_type: EdgeType
    weight: float
    action_hint: str


class RouteAction(BaseModel):
    action: RouteActionType
    node_id: int
    text: str
    device_id: str | None = None


class RoutePlan(BaseModel):
    route_id: str
    start_node: int
    goal_node: int
    via_nodes: list[int] = Field(default_factory=list)
    nodes: list[RouteNode]
    segments: list[RouteSegment]
    actions: list[RouteAction]
    estimated_total_time_seconds: float
    voice_text: str
    reroute_of: str | None = None
    blocked_nodes: list[int] = Field(default_factory=list)
    blocked_edges: list[str] = Field(default_factory=list)


class ScenarioStep(BaseModel):
    index: int
    node_id: int | None = None
    action: str
    status: str = "pending"
    timeout_seconds: int = 30


class ScenarioState(BaseModel):
    scenario_id: str
    scenario_kind: ScenarioKind
    user_id: str
    status: ScenarioStatus
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    current_step_index: int = 0
    route_id: str | None = None
    target_node_id: int | None = None
    assigned_bus_id: str | None = None
    destination_label: str | None = None
    steps: list[ScenarioStep] = Field(default_factory=list)
    last_error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CityConfig(BaseModel):
    enabled: bool = True
    allow_outbound_events: bool = True
    base_url: str
    state_url: str
    event_path: str = "/event"
    access_token: str
    poll_interval_seconds: float = 2.0
    timeout_seconds: float = 3.0
    bus_ring_mapping: dict[str, str] = Field(default_factory=dict)
    stop_index_mapping: dict[str, dict[str, int]] = Field(default_factory=dict)


class CongestionConfig(BaseModel):
    eta_growth_ratio_threshold: float = 1.35
    lap_delta_seconds_threshold: float = 40.0
    min_baseline_seconds: float = 90.0


class RecommendationConfig(BaseModel):
    cold_threshold_c: float = 5.0
    hot_threshold_c: float = 24.0
    high_humidity_threshold: float = 80.0
    low_pressure_threshold_hpa: float = 745.0


class SimulationConfig(BaseModel):
    enabled: bool = True
    auto_bus_updates: bool = False
    auto_point_confirmation: bool = False
    loop_interval_seconds: float = 1.0


class DeepSeekConfig(BaseModel):
    enabled: bool = False
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    model: str = "deepseek-reasoner"
    timeout_seconds: float = 30.0


class TeamConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    team_id: str
    team_name: str
    primary_ring: str
    user_id: str
    selected_confirmation_mode: ConfirmationMode
    face_threshold: float = 0.85
    primary_signal_profile: str
    city: CityConfig
    congestion: CongestionConfig
    recommendation: RecommendationConfig
    simulation: SimulationConfig
    deepseek: DeepSeekConfig = Field(default_factory=DeepSeekConfig)
    auth: dict[str, str] = Field(default_factory=dict)
    ring_stop_order: dict[str, list[int]] = Field(default_factory=dict)
    team_point_mapping: dict[str, list[int]] = Field(default_factory=dict)


class MFCServiceItem(BaseModel):
    service_id: str
    service_name: str
    window_node: int
    queue_prefix: str


class MFCServiceMap(BaseModel):
    services: list[MFCServiceItem]


class RouteGraph(BaseModel):
    nodes: list[RouteNode]
    edges: list[RouteEdge]
    stop_mappings: dict[str, list[int]] = Field(default_factory=dict)
    indoor_mappings: dict[str, list[int]] = Field(default_factory=dict)
    devices: list[DeviceDescriptor] = Field(default_factory=list)
    signal_profiles: list[SignalProfile] = Field(default_factory=list)
    mfc_service_map: MFCServiceMap | None = None


class RuntimeConfig(BaseModel):
    team_config: TeamConfig
    route_graph: RouteGraph


class WeatherSnapshot(BaseModel):
    temperature_c: float
    humidity_pct: float
    pressure_hpa: float
    timestamp: datetime = Field(default_factory=utc_now)
    mode: WeatherMode | None = None
    recommendation_text: str | None = None


class BuildRouteRequest(BaseModel):
    user_id: str
    start_node: int
    goal_node: int
    via_nodes: list[int] = Field(default_factory=list)
    scenario_kind: ScenarioKind = ScenarioKind.WALK


class StartScenarioRequest(BaseModel):
    scenario_kind: ScenarioKind
    user_id: str
    start_node: int
    goal_node: int | None = None
    via_nodes: list[int] = Field(default_factory=list)
    bus_id: str | None = None
    destination_label: str | None = None
    mfc_service_id: str | None = None


class StopScenarioRequest(BaseModel):
    scenario_id: str


class DeviceEventEnvelope(BaseModel):
    type: int
    device_id: str | None = None
    location_id: int | None = None
    user_id: str | None = None
    expected_user_id: str | None = None
    rfid_code: str | None = None
    confidence: float | None = None
    obstacle_type: str | None = None
    reroute_required: bool | None = None
    message: str | None = None
    text: str | None = None
    color: dict[str, int] | None = None
    duration_ms: int | None = None
    frequency_hz: int | None = None
    distance_cm: float | None = None
    timestamp: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ETAResponse(BaseModel):
    buses: list[BusState]
    congestion_flags: dict[str, bool]


class RecommendationResponse(BaseModel):
    weather: WeatherSnapshot | None = None
    congestion_warning: str | None = None


class VoiceRouteParameters(BaseModel):
    user_id: str = ""
    start_point: int | None = None
    target_point: int | None = None
    via_points: list[int] = Field(default_factory=list)
    scenario: str = ""


class VoiceRouteParseRequest(BaseModel):
    transcript: str
    current_user_id: str = ""
    current_start_point: int | None = None
    current_target_point: int | None = None
    current_via_points: list[int] = Field(default_factory=list)
    current_scenario: str = ""


class VoiceRouteParseResponse(BaseModel):
    transcript: str
    params: VoiceRouteParameters
    missing_fields: list[str] = Field(default_factory=list)
    message: str
    model_name: str
    raw_model_json: dict[str, Any] = Field(default_factory=dict)


class AuditRecord(BaseModel):
    timestamp: datetime = Field(default_factory=utc_now)
    level: str = "INFO"
    event: str
    message: str
    scenario_id: str | None = None
    user_id: str | None = None
    route_id: str | None = None
    device_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
