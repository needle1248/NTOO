import logging

from digital_guide.core.models import RouteGraph, TeamConfig
from digital_guide.core.state import AppStateStore
from digital_guide.services.city_state_poller import CityStatePoller
from digital_guide.services.congestion_engine import CongestionEngine
from digital_guide.services.eta_engine import ETAEngine
from digital_guide.services.persistence import JsonPersistence
from digital_guide.services.recommendation_engine import RecommendationEngine


def build_team_config() -> TeamConfig:
    return TeamConfig.model_validate(
        {
            "team_id": "team-01",
            "team_name": "Malevin's Kids",
            "primary_ring": "left_ring",
            "user_id": "user_demo",
            "selected_confirmation_mode": "face",
            "face_threshold": 0.85,
            "primary_signal_profile": "default_type1",
            "city": {
                "enabled": True,
                "allow_outbound_events": False,
                "base_url": "http://192.168.31.63:8000",
                "state_url": "http://192.168.31.63:8000/debug/state",
                "event_path": "/event",
                "access_token": "",
                "bus_ring_mapping": {
                    "1": "left_ring",
                    "2": "right_ring",
                },
                "stop_index_mapping": {
                    "left_ring": {
                        "1": 9,
                        "2": 10,
                        "3": 13,
                        "4": 16,
                        "5": 12,
                        "6": 11,
                        "7": 15,
                        "8": 14,
                    },
                    "right_ring": {
                        "1": 29,
                        "2": 30,
                        "3": 31,
                        "4": 32,
                        "5": 29,
                        "6": 30,
                        "7": 31,
                        "8": 32,
                    },
                },
            },
            "congestion": {
                "eta_growth_ratio_threshold": 1.35,
                "lap_delta_seconds_threshold": 40.0,
                "min_baseline_seconds": 90.0,
            },
            "recommendation": {
                "cold_threshold_c": 5.0,
                "hot_threshold_c": 24.0,
                "high_humidity_threshold": 80.0,
                "low_pressure_threshold_hpa": 745.0,
            },
            "simulation": {
                "enabled": False,
                "auto_bus_updates": False,
                "auto_point_confirmation": False,
                "loop_interval_seconds": 1.0,
            },
            "deepseek": {
                "enabled": False,
                "base_url": "https://api.deepseek.com",
                "api_key": "",
                "model": "deepseek-reasoner",
                "timeout_seconds": 30.0,
            },
        }
    )


def build_poller(tmp_path) -> tuple[CityStatePoller, AppStateStore]:
    graph = RouteGraph.model_validate(
        {
            "nodes": [],
            "edges": [],
            "stop_mappings": {
                "left_ring": [9, 10, 13, 16],
                "right_ring": [29, 30, 31, 32],
            },
            "indoor_mappings": {},
        }
    )
    team_config = build_team_config()
    store = AppStateStore(
        team_config=team_config,
        logger=logging.getLogger("test-city-poller"),
        persistence=JsonPersistence(tmp_path),
    )
    eta_engine = ETAEngine(graph)
    poller = CityStatePoller(
        store=store,
        logger=logging.getLogger("test-city-poller"),
        city_config=team_config.city,
        eta_engine=eta_engine,
        congestion_engine=CongestionEngine(team_config.congestion),
        recommendation_engine=RecommendationEngine(team_config.recommendation),
    )
    return poller, store


def test_city_state_poller_accepts_buses_as_mapping(tmp_path) -> None:
    poller, store = build_poller(tmp_path)
    poller.ingest_state(
        {
            "buses": {
                "bus-alpha": {
                    "ring_id": "left_ring",
                    "current_stop": 9,
                    "timestamp": "2026-04-08T08:16:53Z",
                }
            }
        }
    )

    assert "bus-alpha" in store.buses
    assert store.buses["bus-alpha"].current_stop == 9
    assert store.buses["bus-alpha"].ring_id == "left_ring"


def test_city_state_poller_infers_ring_and_parses_nested_weather(tmp_path) -> None:
    poller, store = build_poller(tmp_path)
    poller.ingest_state(
        {
            "buses": {
                "left-bus": {
                    "stop": 10,
                    "time": "2026-04-08T08:17:10Z",
                }
            },
            "sensors": {
                "mgs-thp80": {
                    "temperature": 12.5,
                    "humidity": 66.0,
                    "pressure": 752.0,
                }
            },
        }
    )

    assert store.buses["left-bus"].ring_id == "left_ring"
    assert store.weather is not None
    assert store.weather.temperature_c == 12.5
    assert store.weather.humidity_pct == 66.0
    assert store.weather.pressure_hpa == 752.0


def test_city_state_poller_maps_city_stop_index_to_internal_stop(tmp_path) -> None:
    poller, store = build_poller(tmp_path)
    poller.ingest_state(
        {
            "buses": {
                "2": {
                    "current_stop": 3,
                    "timestamp": 34,
                }
            }
        }
    )

    assert "2" in store.buses
    assert store.buses["2"].ring_id == "right_ring"
    assert store.buses["2"].current_stop == 31


def test_city_state_poller_prefers_explicit_bus_ring_mapping(tmp_path) -> None:
    poller, store = build_poller(tmp_path)
    poller.ingest_state(
        {
            "buses": {
                "1": {
                    "current_stop": 2,
                    "timestamp": 35,
                }
            }
        }
    )

    assert "1" in store.buses
    assert store.buses["1"].ring_id == "left_ring"
    assert store.buses["1"].current_stop == 10


def test_city_state_poller_anchors_relative_timestamp_and_skips_duplicate_stop(tmp_path) -> None:
    poller, store = build_poller(tmp_path)
    poller.ingest_state(
        {
            "buses": {
                "2": {
                    "current_stop": 1,
                    "timestamp": 22,
                }
            }
        }
    )
    first = store.buses["2"]

    poller.ingest_state(
        {
            "buses": {
                "2": {
                    "current_stop": 1,
                    "timestamp": 22,
                }
            }
        }
    )
    second = store.buses["2"]

    poller.ingest_state(
        {
            "buses": {
                "2": {
                    "current_stop": 2,
                    "timestamp": 34,
                }
            }
        }
    )
    third = store.buses["2"]

    assert first.timestamp.year != 1970
    assert second.timestamp == first.timestamp
    assert second.lap_time_seconds is None
    assert third.current_stop == 30
    assert (third.timestamp - first.timestamp).total_seconds() == 12


def test_city_state_poller_uses_explicit_stop_index_mapping_for_wrapped_right_ring(tmp_path) -> None:
    poller, store = build_poller(tmp_path)
    poller.ingest_state(
        {
            "buses": {
                "2": {
                    "current_stop": 6,
                    "timestamp": 40,
                }
            }
        }
    )

    assert store.buses["2"].ring_id == "right_ring"
    assert store.buses["2"].current_stop == 30
