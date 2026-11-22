"""Deterministic multi-access shortest-path trees."""

from __future__ import annotations

import heapq
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast

import networkx as nx

from walkbike_routes.exceptions import AccessibilityError
from walkbike_routes.models import TravelDirection
from walkbike_routes.numeric import require_finite


@dataclass(frozen=True)
class PathTree:
    """Best cost, path, and selected access point for each reachable node."""

    direction: TravelDirection
    cost_by_node: dict[str, float]
    path_by_node: dict[str, list[str]]
    access_point_by_node: dict[str, str]


@dataclass(frozen=True)
class PathAlternative:
    """One bounded loopless alternative between a node and an access point."""

    cost: float
    access_point_id: str
    path: tuple[str, ...]


@dataclass(frozen=True)
class MultiPathTree:
    """Up to a configured number of near-best alternatives for each reachable node."""

    direction: TravelDirection
    alternatives_by_node: dict[str, tuple[PathAlternative, ...]]


def _edge_weight(graph: nx.Graph[str], u: str, v: str, weight: str) -> float:
    value = graph[u][v].get(weight)
    if value is None:
        raise AccessibilityError(f"edge {u!r}->{v!r} has no {weight!r} cost")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise AccessibilityError(f"edge {u!r}->{v!r} has invalid {weight!r} cost {value!r}")
    return result


def multi_source_paths(
    graph: nx.Graph[str],
    access_points: Mapping[str, str],
    *,
    weight: str,
    direction: TravelDirection,
) -> PathTree:
    """Build deterministic paths between every reachable node and an access point.

    Ties are resolved by access-point ID and then by the lexical node path. For
    ``to_destination`` the returned path runs from each node to its access point;
    for ``from_destination`` it runs from the access point to each node.
    """
    if direction is TravelDirection.BOTH:
        raise AccessibilityError("path trees require one direction, not 'both'")
    valid = sorted(
        (str(access_id), str(node_id))
        for access_id, node_id in access_points.items()
        if str(node_id) in graph
    )
    if not valid:
        return PathTree(direction, {}, {}, {})
    search: nx.Graph[str]
    if direction is TravelDirection.TO_DESTINATION and graph.is_directed():
        search = cast("nx.DiGraph[str]", graph).reverse(copy=False)
    else:
        search = graph

    best: dict[str, tuple[float, str, tuple[str, ...]]] = {}
    queue: list[tuple[float, str, tuple[str, ...], str]] = []
    for access_id, node_id in valid:
        candidate: tuple[float, str, tuple[str, ...]] = (0.0, access_id, (node_id,))
        current = best.get(node_id)
        if current is None or candidate < current:
            best[node_id] = candidate
            heapq.heappush(queue, (*candidate, node_id))

    while queue:
        cost, access_id, search_path, node = heapq.heappop(queue)
        if best.get(node) != (cost, access_id, search_path):
            continue
        for neighbor_raw in sorted(search.neighbors(node), key=str):
            neighbor = str(neighbor_raw)
            edge_cost = _edge_weight(search, node, neighbor, weight)
            candidate = (cost + edge_cost, access_id, (*search_path, neighbor))
            current = best.get(neighbor)
            if current is None or candidate < current:
                best[neighbor] = candidate
                heapq.heappush(queue, (*candidate, neighbor))

    costs: dict[str, float] = {}
    paths: dict[str, list[str]] = {}
    selected: dict[str, str] = {}
    for node, (cost, access_id, search_path) in best.items():
        costs[node] = cost
        selected[node] = access_id
        path = list(search_path)
        if direction is TravelDirection.TO_DESTINATION:
            path.reverse()
        paths[node] = path
    return PathTree(direction, costs, paths, selected)


def multi_source_k_paths(
    graph: nx.Graph[str],
    access_points: Mapping[str, str],
    *,
    weight: str,
    direction: TravelDirection,
    maximum_paths: int,
    maximum_cost_gap: float,
    maximum_cost_ratio: float,
    place: Callable[[str], str] | None = None,
) -> MultiPathTree:
    """Build a bounded multi-label set of near-best loopless paths for every node.

    This is a scalable alternative-choice search, not an exhaustive enumeration
    of mathematical k-shortest simple paths. At most ``maximum_paths`` labels are
    retained at each intermediate node, each within the cost gap of that node's
    best; a node's own alternatives must also be within the cost ratio of its
    best.  A path
    may not visit a place twice; ``place`` maps a graph node to its place (for
    a movement graph, :func:`walkbike_routes.routing.turns.place`), and by
    default each node is its own.
    """
    if direction is TravelDirection.BOTH:
        raise AccessibilityError("multi-path trees require one direction, not 'both'")
    require_finite(maximum_cost_gap, "maximum_cost_gap", error_type=AccessibilityError)
    require_finite(
        maximum_cost_ratio, "maximum_cost_ratio", minimum=1, error_type=AccessibilityError
    )
    if maximum_paths <= 0:
        raise AccessibilityError("maximum_paths must be positive")
    valid = sorted(
        (str(access_id), str(node_id))
        for access_id, node_id in access_points.items()
        if str(node_id) in graph
    )
    if not valid:
        return MultiPathTree(direction, {})
    search: nx.Graph[str]
    if direction is TravelDirection.TO_DESTINATION and graph.is_directed():
        search = cast("nx.DiGraph[str]", graph).reverse(copy=False)
    else:
        search = graph
    where = place or str
    accepted: dict[str, list[tuple[float, str, tuple[str, ...]]]] = {}
    queue: list[tuple[float, str, tuple[str, ...], str, frozenset[str]]] = []
    for access_id, node_id in valid:
        heapq.heappush(queue, (0.0, access_id, (node_id,), node_id, frozenset({where(node_id)})))
    while queue:
        cost, access_id, search_path, node, visited = heapq.heappop(queue)
        alternatives = accepted.setdefault(node, [])
        if len(alternatives) >= maximum_paths:
            continue
        # Only the gap prunes here.  The ratio bound belongs to the whole trip:
        # a detour near the destination is a small share of a long trip's cost
        # though a large share of what remains from this node.
        if alternatives and cost > alternatives[0][0] + maximum_cost_gap:
            continue
        if any(existing[2] == search_path for existing in alternatives):
            continue
        alternatives.append((cost, access_id, search_path))
        for neighbor_raw in sorted(search.neighbors(node), key=str):
            neighbor = str(neighbor_raw)
            at = where(neighbor)
            if at in visited:
                continue
            edge_cost = _edge_weight(search, node, neighbor, weight)
            heapq.heappush(
                queue,
                (cost + edge_cost, access_id, (*search_path, neighbor), neighbor, visited | {at}),
            )
    result: dict[str, tuple[PathAlternative, ...]] = {}
    for node, alternatives in accepted.items():
        converted: list[PathAlternative] = []
        limit = alternatives[0][0] * maximum_cost_ratio
        for cost, access_id, search_path in alternatives:
            if cost > limit:
                continue
            path = (
                tuple(reversed(search_path))
                if direction is TravelDirection.TO_DESTINATION
                else search_path
            )
            converted.append(PathAlternative(cost, access_id, path))
        result[node] = tuple(converted)
    return MultiPathTree(direction, result)
