from __future__ import annotations

import heapq
import uuid
from collections import defaultdict

from digital_guide.core.models import EdgeType, RouteAction, RouteActionType, RouteEdge, RouteGraph, RouteNode, RoutePlan, RouteSegment, ScenarioKind


class RouteEngine:
    def __init__(self, graph: RouteGraph) -> None:
        self.graph = graph
        self.node_by_id = {node.node_id: node for node in graph.nodes}
        self._adjacency = self._build_adjacency(graph.edges)

    def _build_adjacency(self, edges: list[RouteEdge]) -> dict[int, list[tuple[int, RouteEdge]]]:
        adjacency: dict[int, list[tuple[int, RouteEdge]]] = defaultdict(list)
        for edge in edges:
            adjacency[edge.from_node].append((edge.to_node, edge))
            if edge.bidirectional:
                reverse = RouteEdge(
                    from_node=edge.to_node,
                    to_node=edge.from_node,
                    edge_type=edge.edge_type,
                    weight=edge.weight,
                    bidirectional=edge.bidirectional,
                )
                adjacency[edge.to_node].append((edge.from_node, reverse))
        return adjacency

    def build_route(
        self,
        start_node: int,
        goal_node: int,
        via_nodes: list[int] | None = None,
        blocked_nodes: set[int] | None = None,
        blocked_edges: set[tuple[int, int]] | None = None,
        scenario_kind: ScenarioKind = ScenarioKind.WALK,
        reroute_of: str | None = None,
    ) -> RoutePlan:
        via_nodes = via_nodes or []
        blocked_nodes = blocked_nodes or set()
        blocked_edges = blocked_edges or set()
        ordered_ids: list[int] = []
        targets = [start_node, *via_nodes, goal_node]
        total_weight = 0.0
        all_segments: list[RouteSegment] = []

        for src, dst in zip(targets, targets[1:]):
            path_ids, path_edges = self._shortest_path(src, dst, blocked_nodes, blocked_edges)
            if ordered_ids:
                path_ids = path_ids[1:]
            ordered_ids.extend(path_ids)
            all_segments.extend(path_edges)
            total_weight += sum(edge.weight for edge in path_edges)

        nodes = [self.node_by_id[node_id] for node_id in ordered_ids]
        actions = self._to_actions(nodes, all_segments, scenario_kind)
        voice_text = self._voice_summary(nodes, scenario_kind)
        return RoutePlan(
            route_id=f"route-{uuid.uuid4().hex[:8]}",
            start_node=start_node,
            goal_node=goal_node,
            via_nodes=via_nodes,
            nodes=nodes,
            segments=all_segments,
            actions=actions,
            estimated_total_time_seconds=total_weight * 25,
            voice_text=voice_text,
            reroute_of=reroute_of,
            blocked_nodes=sorted(blocked_nodes),
            blocked_edges=[f"{a}->{b}" for a, b in sorted(blocked_edges)],
        )

    def _shortest_path(
        self,
        start_node: int,
        goal_node: int,
        blocked_nodes: set[int],
        blocked_edges: set[tuple[int, int]],
    ) -> tuple[list[int], list[RouteSegment]]:
        heap: list[tuple[float, int]] = [(0.0, start_node)]
        distances = {start_node: 0.0}
        previous: dict[int, tuple[int, RouteEdge]] = {}

        while heap:
            distance, node = heapq.heappop(heap)
            if node == goal_node:
                break
            if distance > distances.get(node, float("inf")):
                continue
            for neighbor, edge in self._adjacency.get(node, []):
                if neighbor in blocked_nodes or node in blocked_nodes:
                    continue
                if (node, neighbor) in blocked_edges:
                    continue
                candidate = distance + edge.weight
                if candidate < distances.get(neighbor, float("inf")):
                    distances[neighbor] = candidate
                    previous[neighbor] = (node, edge)
                    heapq.heappush(heap, (candidate, neighbor))

        if goal_node not in distances:
            raise ValueError(f"Нет маршрута от точки {start_node} до точки {goal_node}")

        node_ids = [goal_node]
        segments: list[RouteSegment] = []
        cursor = goal_node
        while cursor != start_node:
            parent, edge = previous[cursor]
            node_ids.append(parent)
            segments.append(
                RouteSegment(
                    from_node=edge.from_node,
                    to_node=edge.to_node,
                    edge_type=edge.edge_type,
                    weight=edge.weight,
                    action_hint=edge.edge_type.value,
                )
            )
            cursor = parent
        node_ids.reverse()
        segments.reverse()
        return node_ids, segments

    def _to_actions(self, nodes: list[RouteNode], segments: list[RouteSegment], scenario_kind: ScenarioKind) -> list[RouteAction]:
        actions: list[RouteAction] = []
        first_indoor = True
        for node in nodes[1:]:
            actions.append(
                RouteAction(
                    action=RouteActionType.GO_TO_POINT,
                    node_id=node.node_id,
                    text=f"Двигайтесь к точке {node.node_id}",
                    device_id=node.device_id,
                )
            )
            if node.node_type in {"nav_point", "stop", "indoor"}:
                actions.append(
                    RouteAction(
                        action=RouteActionType.WAIT_CONFIRMATION,
                        node_id=node.node_id,
                        text=f"Ожидайте подтверждение на точке {node.node_id}",
                        device_id=node.device_id,
                    )
                )
        for index, segment in enumerate(segments):
            target = self.node_by_id[segment.to_node]
            source = self.node_by_id[segment.from_node]
            if segment.edge_type == EdgeType.BUS:
                if index == 0 or segments[index - 1].edge_type != EdgeType.BUS:
                    actions.append(RouteAction(action=RouteActionType.BOARD_BUS, node_id=source.node_id, text=f"Садитесь в автобус на остановке {source.node_id}"))
                if index == len(segments) - 1 or segments[index + 1].edge_type != EdgeType.BUS:
                    actions.append(RouteAction(action=RouteActionType.EXIT_BUS, node_id=target.node_id, text=f"Выходите из автобуса на остановке {target.node_id}"))
            if segment.edge_type == EdgeType.TRANSFER:
                actions.append(RouteAction(action=RouteActionType.SWITCH_RING, node_id=target.node_id, text=f"Перейдите на другое кольцо через точку {target.node_id}"))
            if segment.edge_type == EdgeType.INDOOR:
                action = RouteActionType.ENTER_MFC if first_indoor else RouteActionType.GO_TO_SERVICE_WINDOW
                text = "Войдите в МФЦ" if first_indoor else "Двигайтесь к окну обслуживания"
                actions.append(RouteAction(action=action, node_id=target.node_id, text=f"{text} через точку {target.node_id}", device_id=target.device_id))
                first_indoor = False
        if scenario_kind in {ScenarioKind.MFC, ScenarioKind.INDOOR}:
            for action in actions:
                if action.action == RouteActionType.GO_TO_POINT:
                    action.text = action.text.replace("Двигайтесь к точке", "Следуйте по свету к точке")
        return actions

    def _voice_summary(self, nodes: list[RouteNode], scenario_kind: ScenarioKind) -> str:
        if not nodes:
            return "Маршрут пуст."
        if scenario_kind in {ScenarioKind.TRANSPORT, ScenarioKind.BUS}:
            return f"Подготовлен маршрут от точки {nodes[0].node_id} до точки {nodes[-1].node_id} с автобусными участками."
        if scenario_kind in {ScenarioKind.MFC, ScenarioKind.INDOOR}:
            return f"Подготовлен indoor-маршрут от точки {nodes[0].node_id} до сервисной точки {nodes[-1].node_id}."
        if scenario_kind == ScenarioKind.MIXED:
            return f"Подготовлен смешанный маршрут от точки {nodes[0].node_id} до точки {nodes[-1].node_id}."
        return f"Подготовлен пеший маршрут от точки {nodes[0].node_id} до точки {nodes[-1].node_id}."
