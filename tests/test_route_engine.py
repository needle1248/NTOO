from digital_guide.core.models import EdgeType, RouteGraph, RouteNode, RouteEdge, ScenarioKind
from digital_guide.services.route_engine import RouteEngine


def build_graph() -> RouteGraph:
    return RouteGraph(
        stop_mappings={"left_ring": [9, 10, 13, 16]},
        indoor_mappings={},
        nodes=[
            RouteNode(node_id=1, label="Start", node_type="nav_point", ring_id="left_ring", command_id=1, device_id="d1"),
            RouteNode(node_id=2, label="Mid", node_type="nav_point", ring_id="left_ring", command_id=2, device_id="d2"),
            RouteNode(node_id=3, label="Alt", node_type="nav_point", ring_id="left_ring", command_id=3, device_id="d3"),
            RouteNode(node_id=4, label="Goal", node_type="nav_point", ring_id="left_ring", command_id=4, device_id="d4"),
            RouteNode(node_id=9, label="Stop 9", node_type="stop", ring_id="left_ring"),
            RouteNode(node_id=10, label="Stop 10", node_type="stop", ring_id="left_ring"),
        ],
        edges=[
            RouteEdge(from_node=1, to_node=2, edge_type=EdgeType.PEDESTRIAN, weight=1),
            RouteEdge(from_node=2, to_node=4, edge_type=EdgeType.PEDESTRIAN, weight=1),
            RouteEdge(from_node=1, to_node=3, edge_type=EdgeType.PEDESTRIAN, weight=1),
            RouteEdge(from_node=3, to_node=4, edge_type=EdgeType.PEDESTRIAN, weight=3),
            RouteEdge(from_node=2, to_node=9, edge_type=EdgeType.PEDESTRIAN, weight=1),
            RouteEdge(from_node=9, to_node=10, edge_type=EdgeType.BUS, weight=1, bidirectional=False),
            RouteEdge(from_node=10, to_node=4, edge_type=EdgeType.PEDESTRIAN, weight=5),
        ],
    )


def test_route_engine_builds_shortest_path() -> None:
    engine = RouteEngine(build_graph())
    route = engine.build_route(start_node=1, goal_node=4)
    assert [node.node_id for node in route.nodes] == [1, 2, 4]
    assert route.estimated_total_time_seconds == 50


def test_route_engine_reroutes_around_blocked_node() -> None:
    engine = RouteEngine(build_graph())
    route = engine.build_route(start_node=1, goal_node=4, blocked_nodes={2})
    assert [node.node_id for node in route.nodes] == [1, 3, 4]
    assert route.blocked_nodes == [2]


def test_transport_route_contains_bus_actions() -> None:
    engine = RouteEngine(build_graph())
    route = engine.build_route(start_node=1, goal_node=10, scenario_kind=ScenarioKind.TRANSPORT)
    actions = [action.action.value for action in route.actions]
    assert "board_bus" in actions
    assert "exit_bus" in actions
