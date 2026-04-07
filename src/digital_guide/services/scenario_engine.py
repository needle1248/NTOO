from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from digital_guide.core.models import (
    AuditRecord,
    ConfirmationMode,
    DeviceEventEnvelope,
    ObstacleEvent,
    RFIDEvent,
    FaceEvent,
    RouteActionType,
    ScenarioKind,
    ScenarioState,
    ScenarioStatus,
    ScenarioStep,
    StartScenarioRequest,
)
from digital_guide.core.state import AppStateStore
from digital_guide.services.obstacle_engine import ObstacleEngine
from digital_guide.services.route_engine import RouteEngine


class ScenarioEngine:
    def __init__(
        self,
        store: AppStateStore,
        route_engine: RouteEngine,
        obstacle_engine: ObstacleEngine,
        recommendation_engine,
        eta_engine,
        congestion_engine,
        device_gateway,
        voice_engine,
        city_event_sender,
        session_manager,
    ) -> None:
        self.store = store
        self.route_engine = route_engine
        self.obstacle_engine = obstacle_engine
        self.recommendation_engine = recommendation_engine
        self.eta_engine = eta_engine
        self.congestion_engine = congestion_engine
        self.device_gateway = device_gateway
        self.voice_engine = voice_engine
        self.city_event_sender = city_event_sender
        self.session_manager = session_manager
        self.logger: logging.Logger = store.logger
        self._debounce_cache: dict[tuple[str, str], datetime] = {}

    def start(self, request: StartScenarioRequest) -> ScenarioState:
        goal_node = request.goal_node or self._resolve_mfc_goal(request.mfc_service_id)
        route = self.route_engine.build_route(
            start_node=request.start_node,
            goal_node=goal_node,
            via_nodes=request.via_nodes,
            blocked_nodes=self.obstacle_engine.blocked_nodes(),
            scenario_kind=request.scenario_kind,
        )
        self.store.active_route = route
        steps = [
            ScenarioStep(index=index, node_id=action.node_id, action=action.action.value, timeout_seconds=self._timeout_for(action.action))
            for index, action in enumerate(route.actions)
        ]
        scenario = ScenarioState(
            scenario_id=f"scn-{uuid.uuid4().hex[:8]}",
            scenario_kind=request.scenario_kind,
            user_id=request.user_id,
            status=ScenarioStatus.RUNNING,
            route_id=route.route_id,
            target_node_id=goal_node,
            destination_label=request.destination_label,
            assigned_bus_id=request.bus_id,
            steps=steps,
            metadata={"confirmation_mode": self.store.team_config.selected_confirmation_mode.value},
        )
        self.session_manager.set_active_scenario(scenario)
        self.store.append_log(
            AuditRecord(
                event="scenario_started",
                message=f"Сценарий {scenario.scenario_kind.value} запущен.",
                scenario_id=scenario.scenario_id,
                user_id=scenario.user_id,
                route_id=route.route_id,
                payload={"route": route.model_dump(mode="json")},
            )
        )
        self._activate_next_point(scenario)
        return scenario

    def stop(self, scenario_id: str) -> ScenarioState:
        scenario = self.store.scenarios[scenario_id]
        scenario.status = ScenarioStatus.STOPPED
        scenario.updated_at = datetime.now(timezone.utc)
        self.store.append_log(AuditRecord(event="scenario_stopped", message=f"Сценарий {scenario_id} остановлен.", scenario_id=scenario_id))
        return scenario

    def handle_device_event(self, payload: DeviceEventEnvelope) -> dict:
        active = self.active_scenario()
        if payload.type == 5:
            obstacle = ObstacleEvent(
                location_id=payload.location_id or -1,
                obstacle_type=payload.obstacle_type or "unknown",
                reroute_required=bool(payload.reroute_required),
                message=payload.message or "Обнаружено препятствие",
                active=True,
            )
            self.obstacle_engine.upsert(obstacle)
            self.store.append_log(
                AuditRecord(
                    event="obstacle_detected",
                    message=obstacle.message,
                    scenario_id=active.scenario_id if active else None,
                    payload=obstacle.model_dump(mode="json"),
                )
            )
            if active and self.store.active_route and self.obstacle_engine.route_impacted([node.node_id for node in self.store.active_route.nodes]):
                rerouted = self.route_engine.build_route(
                    start_node=self._current_node(active),
                    goal_node=active.target_node_id or self.store.active_route.goal_node,
                    blocked_nodes=self.obstacle_engine.blocked_nodes(),
                    scenario_kind=active.scenario_kind,
                    reroute_of=self.store.active_route.route_id,
                )
                self.store.active_route = rerouted
                self.store.append_log(
                    AuditRecord(
                        event="route_rerouted",
                        message="Маршрут перестроен из-за препятствия.",
                        scenario_id=active.scenario_id,
                        route_id=rerouted.route_id,
                        payload=rerouted.model_dump(mode="json"),
                    )
                )
                return {"status": "rerouted", "route_id": rerouted.route_id}
            return {"status": "stored"}
        if not active:
            return {"status": "ignored", "reason": "no_active_scenario"}
        if payload.type == 4:
            if not self._accept_rfid(payload, active):
                return {"status": "rejected"}
            event = RFIDEvent(device_id=payload.device_id or "unknown", location_id=payload.location_id, user_id=payload.user_id, rfid_code=payload.rfid_code or "")
            self.store.queue_city_event(event.model_dump(mode="json"))
            return self._advance(active, reason="rfid_confirmed")
        if payload.type == 6:
            if not self._accept_face(payload, active):
                return {"status": "rejected"}
            event = FaceEvent(
                device_id=payload.device_id or "unknown",
                user_id=payload.user_id or "",
                expected_user_id=active.user_id,
                confidence=payload.confidence or 0.0,
            )
            self.store.queue_city_event(event.model_dump(mode="json"))
            return self._advance(active, reason="face_confirmed")
        if payload.type == 99 and payload.distance_cm is not None:
            vibro_state = "on" if payload.distance_cm < 120 else "off"
            self.device_gateway.queue_signal(payload.device_id or "vibro-01", {"type": "vibration", "state": vibro_state})
            self.store.append_log(
                AuditRecord(
                    event="vibro_toggle",
                    message=f"Виброплатформа переключена в состояние {vibro_state}.",
                    scenario_id=active.scenario_id,
                    device_id=payload.device_id,
                    payload={"distance_cm": payload.distance_cm, "state": vibro_state},
                )
            )
            return {"status": vibro_state}
        return {"status": "ignored"}

    def active_scenario(self) -> ScenarioState | None:
        for scenario in self.store.scenarios.values():
            if scenario.status in {ScenarioStatus.RUNNING, ScenarioStatus.WAITING_CONFIRMATION, ScenarioStatus.WAITING_BUS}:
                return scenario
        return None

    def _accept_rfid(self, payload: DeviceEventEnvelope, scenario: ScenarioState) -> bool:
        if self.store.team_config.selected_confirmation_mode not in {ConfirmationMode.RFID, ConfirmationMode.FACE}:
            return False
        key = ("rfid", payload.rfid_code or "")
        if self._duplicate(key):
            return False
        return True

    def _accept_face(self, payload: DeviceEventEnvelope, scenario: ScenarioState) -> bool:
        key = ("face", f"{payload.user_id}:{payload.device_id}")
        if self._duplicate(key):
            return False
        return (
            (payload.confidence or 0.0) >= self.store.team_config.face_threshold
            and payload.user_id == scenario.user_id
        )

    def _duplicate(self, key: tuple[str, str]) -> bool:
        now = datetime.now(timezone.utc)
        previous = self._debounce_cache.get(key)
        self._debounce_cache[key] = now
        return bool(previous and (now - previous).total_seconds() < 2.0)

    def _advance(self, scenario: ScenarioState, reason: str) -> dict:
        if scenario.current_step_index < len(scenario.steps):
            scenario.steps[scenario.current_step_index].status = "done"
        scenario.current_step_index += 1
        scenario.updated_at = datetime.now(timezone.utc)
        self.store.append_log(
            AuditRecord(
                event="point_confirmed",
                message=f"Шаг сценария подтвержден по причине {reason}.",
                scenario_id=scenario.scenario_id,
                user_id=scenario.user_id,
                payload={"step": scenario.current_step_index, "reason": reason},
            )
        )
        if scenario.current_step_index >= len(scenario.steps):
            scenario.status = ScenarioStatus.COMPLETED
            self.session_manager.complete_scenario(scenario.scenario_id)
            return {"status": "completed"}
        self._activate_next_point(scenario)
        return {"status": "advanced", "step": scenario.current_step_index}

    def _activate_next_point(self, scenario: ScenarioState) -> None:
        if not self.store.active_route:
            return
        while scenario.current_step_index < len(scenario.steps):
            step = scenario.steps[scenario.current_step_index]
            if step.action != RouteActionType.GO_TO_POINT.value:
                scenario.current_step_index += 1
                continue
            node = next(node for node in self.store.active_route.nodes if node.node_id == step.node_id)
            command = {"action": "activate", "node_id": node.node_id, "command_id": node.command_id}
            if node.device_id:
                self.device_gateway.queue_signal(node.device_id, command)
            scenario.status = ScenarioStatus.WAITING_CONFIRMATION
            self.store.append_log(
                AuditRecord(
                    event="point_activated",
                    message=f"Точка {node.node_id} активирована.",
                    scenario_id=scenario.scenario_id,
                    user_id=scenario.user_id,
                    device_id=node.device_id,
                    payload=command,
                )
            )
            break

    def _timeout_for(self, action: RouteActionType) -> int:
        if action in {RouteActionType.BOARD_BUS, RouteActionType.EXIT_BUS}:
            return 120
        if action == RouteActionType.WAIT_CONFIRMATION:
            return 45
        return 30

    def _resolve_mfc_goal(self, service_id: str | None) -> int:
        if not service_id or not self.route_engine.graph.mfc_service_map:
            raise ValueError("Для indoor-сценария МФЦ требуется service_id")
        for item in self.route_engine.graph.mfc_service_map.services:
            if item.service_id == service_id:
                return item.window_node
        raise ValueError(f"Неизвестный service_id МФЦ: {service_id}")

    def _current_node(self, scenario: ScenarioState) -> int:
        if not self.store.active_route:
            raise ValueError("Нет активного маршрута для перестроения")
        point_actions = [step.node_id for step in scenario.steps if step.action == RouteActionType.GO_TO_POINT.value and step.node_id is not None]
        if not point_actions:
            return self.store.active_route.start_node
        completed = max(scenario.current_step_index - 1, -1)
        if completed < 0:
            return self.store.active_route.start_node
        index = min(completed, len(point_actions) - 1)
        return point_actions[index]
