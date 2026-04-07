from __future__ import annotations

import logging

from digital_guide.core.models import AuditRecord, ScenarioState, ScenarioStatus
from digital_guide.core.state import AppStateStore


class SessionManager:
    def __init__(self, store: AppStateStore, logger: logging.Logger) -> None:
        self.store = store
        self.logger = logger

    def set_active_scenario(self, scenario: ScenarioState) -> None:
        self.store.scenarios[scenario.scenario_id] = scenario
        self.store.append_log(
            AuditRecord(
                event="scenario_registered",
                message=f"Сценарий {scenario.scenario_id} сохранен.",
                scenario_id=scenario.scenario_id,
                user_id=scenario.user_id,
                payload=scenario.model_dump(mode="json"),
            )
        )

    def complete_scenario(self, scenario_id: str) -> None:
        scenario = self.store.scenarios[scenario_id]
        scenario.status = ScenarioStatus.COMPLETED
        self.store.append_log(
            AuditRecord(
                event="scenario_completed",
                message=f"Сценарий {scenario_id} завершен.",
                scenario_id=scenario_id,
                user_id=scenario.user_id,
            )
        )
