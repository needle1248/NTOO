from __future__ import annotations

from digital_guide.core.models import CongestionConfig


class CongestionEngine:
    def __init__(self, config: CongestionConfig) -> None:
        self.config = config

    def is_congested(self, baseline_eta: float | None, current_eta: float | None, baseline_lap: float | None, current_lap: float | None) -> bool:
        if baseline_eta and current_eta and baseline_eta >= self.config.min_baseline_seconds:
            if current_eta / baseline_eta >= self.config.eta_growth_ratio_threshold:
                return True
        if baseline_lap and current_lap and baseline_lap >= self.config.min_baseline_seconds:
            if current_lap - baseline_lap >= self.config.lap_delta_seconds_threshold:
                return True
        return False

    def warning_message(self) -> str:
        return "На вашем кольце образовалась пробка, рекомендуем временно воздержаться от поездок и остаться дома."
