from __future__ import annotations

from digital_guide.core.models import RecommendationConfig, WeatherMode, WeatherSnapshot


class RecommendationEngine:
    def __init__(self, config: RecommendationConfig) -> None:
        self.config = config

    def evaluate(self, temperature_c: float, humidity_pct: float, pressure_hpa: float) -> WeatherSnapshot:
        if temperature_c < self.config.cold_threshold_c:
            mode = WeatherMode.COLD
            text = "Теплая куртка и шапка."
        elif temperature_c > self.config.hot_threshold_c:
            mode = WeatherMode.HOT
            text = "Легкая одежда и вода с собой."
        else:
            mode = WeatherMode.NORMAL
            text = "Достаточно легкой куртки."

        if humidity_pct >= self.config.high_humidity_threshold:
            text += " Возьмите зонт."
        if pressure_hpa <= self.config.low_pressure_threshold_hpa:
            text += " Учитывайте возможный дискомфорт из-за низкого давления."
        return WeatherSnapshot(
            temperature_c=temperature_c,
            humidity_pct=humidity_pct,
            pressure_hpa=pressure_hpa,
            mode=mode,
            recommendation_text=text,
        )
