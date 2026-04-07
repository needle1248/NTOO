from digital_guide.core.models import CongestionConfig
from digital_guide.services.congestion_engine import CongestionEngine


def test_congestion_detects_eta_growth() -> None:
    engine = CongestionEngine(CongestionConfig(eta_growth_ratio_threshold=1.3, lap_delta_seconds_threshold=40, min_baseline_seconds=90))
    assert engine.is_congested(baseline_eta=100, current_eta=135, baseline_lap=100, current_lap=120) is True


def test_congestion_ignores_small_baseline() -> None:
    engine = CongestionEngine(CongestionConfig(eta_growth_ratio_threshold=1.3, lap_delta_seconds_threshold=40, min_baseline_seconds=90))
    assert engine.is_congested(baseline_eta=50, current_eta=90, baseline_lap=50, current_lap=120) is False

