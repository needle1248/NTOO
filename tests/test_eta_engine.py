from datetime import datetime, timedelta, timezone

from digital_guide.core.models import EdgeType, RouteEdge, RouteGraph, RouteNode
from digital_guide.services.eta_engine import ETAEngine


def build_graph() -> RouteGraph:
    return RouteGraph(
        stop_mappings={"left_ring": [9, 10, 13, 16]},
        indoor_mappings={},
        nodes=[
            RouteNode(node_id=9, label="Stop 9", node_type="stop", ring_id="left_ring"),
            RouteNode(node_id=10, label="Stop 10", node_type="stop", ring_id="left_ring"),
            RouteNode(node_id=13, label="Stop 13", node_type="stop", ring_id="left_ring"),
            RouteNode(node_id=16, label="Stop 16", node_type="stop", ring_id="left_ring"),
        ],
        edges=[
            RouteEdge(from_node=9, to_node=10, edge_type=EdgeType.BUS, weight=1, bidirectional=False),
            RouteEdge(from_node=10, to_node=13, edge_type=EdgeType.BUS, weight=1, bidirectional=False),
            RouteEdge(from_node=13, to_node=16, edge_type=EdgeType.BUS, weight=1, bidirectional=False),
            RouteEdge(from_node=16, to_node=9, edge_type=EdgeType.BUS, weight=1, bidirectional=False),
        ],
    )


def test_eta_engine_updates_segment_averages_with_skipped_stop() -> None:
    engine = ETAEngine(build_graph())
    start = datetime(2026, 4, 7, 10, 0, tzinfo=timezone.utc)
    engine.update_bus("bus-1", "left_ring", 9, start)
    engine.update_bus("bus-1", "left_ring", 13, start + timedelta(seconds=40))
    eta = engine.estimate_eta("left_ring", 13, 16)
    assert round(eta, 1) == 20.0


def test_eta_engine_computes_lap_average_after_wrap() -> None:
    engine = ETAEngine(build_graph())
    start = datetime(2026, 4, 7, 10, 0, tzinfo=timezone.utc)
    engine.update_bus("bus-1", "left_ring", 9, start)
    engine.update_bus("bus-1", "left_ring", 10, start + timedelta(seconds=20))
    engine.update_bus("bus-1", "left_ring", 13, start + timedelta(seconds=40))
    engine.update_bus("bus-1", "left_ring", 16, start + timedelta(seconds=60))
    state = engine.update_bus("bus-1", "left_ring", 9, start + timedelta(seconds=80))
    assert state.rolling_lap_average_seconds == 80.0

