"""Directed edge-state routing with injected link and movement costs.

Ties go to the state that sorts first by name, so a run does not depend on
set or hash order.  Immediate U-turns are excluded in both directions.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Callable, Iterable, Mapping
from typing import Any

import networkx as nx

EdgeWeight = Callable[[str, str, Mapping[str, Any]], float | None]
TransitionCost = Callable[[str, str, str], float]


def _cost(value: float | None, name: str) -> float:
    if value is None:
        return math.inf
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} cost must be nonnegative and finite")
    return value


def transition_arrival_paths(
    graph: nx.DiGraph[str],
    destination_nodes: Iterable[str],
    origin_nodes: Iterable[str],
    edge_weight: Callable[[str, str, Mapping[str, Any]], float | None],
    transition_cost: TransitionCost,
) -> tuple[dict[str, float], dict[str, list[str]]]:
    """Find directed paths whose cost depends on consecutive edge pairs.

    The search runs backward from the destinations over states ``(u, v)``
    representing a traversed directed edge.  This is the edge-state equivalent
    of multi-source Dijkstra and avoids materializing a much larger line graph.
    """
    valid_destinations = {
        str(destination) for destination in destination_nodes if str(destination) in graph
    }
    if not valid_destinations:
        raise ValueError("no destination node is present in the graph")

    distances: dict[tuple[str, str], float] = {}
    next_state: dict[tuple[str, str], tuple[str, str]] = {}
    queue: list[tuple[float, tuple[str, str]]] = []
    for destination in valid_destinations:
        for raw_previous in graph.predecessors(destination):
            previous = str(raw_previous)
            state = (previous, destination)
            if state not in distances:
                distances[state] = 0.0
                heapq.heappush(queue, (0.0, state))

    while queue:
        distance, state = heapq.heappop(queue)
        if distance != distances.get(state):
            continue
        u, v = state
        attrs = graph[u][v]
        edge_cost = edge_weight(u, v, attrs)
        if edge_cost is None:
            continue
        if edge_cost < 0 or not math.isfinite(edge_cost):
            raise ValueError("edge cost must be nonnegative and finite")
        for raw_previous in graph.predecessors(u):
            previous = str(raw_previous)
            if previous == v:
                continue
            penalty = float(transition_cost(previous, u, v))
            if penalty < 0 or not math.isfinite(penalty):
                raise ValueError("transition cost must be nonnegative and finite")
            predecessor = (previous, u)
            new_distance = distance + edge_cost + penalty
            if new_distance < distances.get(predecessor, math.inf):
                distances[predecessor] = new_distance
                next_state[predecessor] = state
                heapq.heappush(queue, (new_distance, predecessor))

    origin_distances: dict[str, float] = {}
    paths: dict[str, list[str]] = {}
    for origin in set(map(str, origin_nodes)):
        if origin not in graph:
            continue
        if origin in valid_destinations:
            origin_distances[origin] = 0.0
            paths[origin] = [origin]
            continue
        choices: list[tuple[float, tuple[str, str]]] = []
        for raw_following in graph.successors(origin):
            following = str(raw_following)
            state = (origin, following)
            if state not in distances:
                continue
            edge_cost = edge_weight(origin, following, graph[origin][following])
            if edge_cost is None:
                continue
            if edge_cost < 0 or not math.isfinite(edge_cost):
                raise ValueError("edge cost must be nonnegative and finite")
            choices.append((edge_cost + distances[state], state))
        if not choices:
            continue
        cost, state = min(choices, key=lambda item: item[0])
        path = [origin, state[1]]
        while state in next_state:
            state = next_state[state]
            path.append(state[1])
        if path[-1] not in valid_destinations:
            raise AssertionError("turn-aware arrival path did not end at a destination")
        origin_distances[origin] = cost
        paths[origin] = path
    return origin_distances, paths


def transition_departure_paths(
    graph: nx.DiGraph[str],
    gate_nodes: Iterable[str],
    origin_nodes: Iterable[str],
    edge_weight: EdgeWeight,
    transition_cost: TransitionCost,
) -> tuple[dict[str, float], dict[str, list[str]]]:
    """Forward Dijkstra from direction-valid departure gates over edge states."""
    distances: dict[tuple[str, str], float] = {}
    previous_state: dict[tuple[str, str], tuple[str, str]] = {}
    queue: list[tuple[float, tuple[str, str]]] = []
    valid_gates = {str(gate) for gate in gate_nodes if str(gate) in graph}
    for gate in sorted(valid_gates):
        for following in sorted(map(str, graph.successors(gate))):
            state = (gate, str(following))
            distance = _cost(edge_weight(gate, str(following), graph[gate][following]), "edge")
            if not math.isfinite(distance):
                continue
            if distance < distances.get(state, math.inf):
                distances[state] = distance
                heapq.heappush(queue, (distance, state))
    while queue:
        distance, state = heapq.heappop(queue)
        if distance != distances.get(state):
            continue
        u, v = state
        for following in graph.successors(v):
            following = str(following)
            if following == u:
                continue
            next_item = (v, following)
            new_distance = (
                distance
                + _cost(edge_weight(v, following, graph[v][following]), "edge")
                + _cost(transition_cost(u, v, following), "transition")
            )
            if new_distance < distances.get(next_item, math.inf):
                distances[next_item] = new_distance
                previous_state[next_item] = state
                heapq.heappush(queue, (new_distance, next_item))
    origin_distances: dict[str, float] = {}
    paths: dict[str, list[str]] = {}
    best_state_by_node: dict[str, tuple[float, tuple[str, str]]] = {}
    for state, distance in distances.items():
        node = state[1]
        if (distance, state) < best_state_by_node.get(node, (math.inf, state)):  # ties by name
            best_state_by_node[node] = (distance, state)
    for origin in set(map(str, origin_nodes)):
        if origin in valid_gates:
            origin_distances[origin] = 0.0
            paths[origin] = [origin]
            continue
        choice = best_state_by_node.get(origin)
        if choice is None:
            continue
        cost, state = choice
        states = [state]
        while state in previous_state:
            state = previous_state[state]
            states.append(state)
        states.reverse()
        path = [states[0][0], states[0][1]]
        path.extend(item[1] for item in states[1:])
        if path[0] not in valid_gates:
            raise AssertionError("turn-aware departure did not start at a valid gate")
        origin_distances[origin] = cost
        paths[origin] = path
    return origin_distances, paths
