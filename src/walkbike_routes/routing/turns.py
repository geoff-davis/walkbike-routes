"""Turn costs for the node-based searches, by expanding the graph to movements.

Each node of the expanded graph is a state, "arrived at ``v`` from ``u``",
and each edge is a link ridden from that state, carrying the link's own
attributes plus the cost of the movement at ``v`` onto it.  The ordinary
searches (single best and near-best) then charge turns without knowing about
them, and a path through the states measures like any other path.

The original nodes stay as start nodes, with an edge to every state leaving
them and no movement cost: a trip that starts at a node has made no turn.  A
route ends at a sink, one per original node, reached from every state
arriving there by a virtual link of zero cost; search to :func:`sink` ids.
Virtual links and the states are marked ``virtual`` so that measuring a path
and listing reachable nodes skip them.  Turning straight back (``u-v-u``) is
not allowed, as for cars.

A junction drawn as several nodes (a signal with separate carriageways and
short links between them) can be treated as one place: give the transition
a ``clusters`` mapping of node to junction id and a ``through(a, b, x, w)``
method costing the whole passage, from link ``a-b`` that entered the
junction to link ``x-w`` that leaves it.  A route inside a junction keeps
the link it entered by in its state, moves between the junction's nodes at
no movement cost, and pays one movement on leaving.  Leaving back along the
link it entered by is not allowed.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import geopandas as gpd
import networkx as nx
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge

from walkbike_routes.routing.costs import CostComponents

STATE = "\x1f"
SINK = "\x1e"
ENTRY = "\x1d"  # inside a junction: "entered by a-b, now at x" is a ENTRY b STATE x
MOVEMENT_FIELDS = (
    "turn_cost_s", "crossing_cost_s", "control_cost_s", "transition_cost_s", "stress_cost_s",
)  # fmt: skip

Transition = Callable[[str, str, str], CostComponents]
HEADING_M = 10.0  # headings are measured over this much of each link next to the node


def state(u: str, v: str) -> str:
    """The expanded node for having arrived at ``v`` from ``u``."""
    return f"{u}{STATE}{v}"


def inside(a: str, b: str, x: str) -> str:
    """The expanded node for being at ``x`` in a junction entered along link ``a-b``."""
    return f"{a}{ENTRY}{b}{STATE}{x}"


def sink(node: str) -> str:
    """The expanded node where routes ending at ``node`` finish."""
    return f"{SINK}{node}"


def turn_graph(
    graph: nx.DiGraph[str], transition: Transition, *, also: tuple[str, ...] = ()
) -> nx.DiGraph[str]:
    """The movement graph of ``graph``, with ``transition(u, v, w)`` charged moving u-v-w.

    The transition's components are added to the attributes of link ``v-w``
    for that movement, and to its ``generalized_cost_s`` and any cost fields
    named in ``also`` (such as a screen's treated cost).  Physical time is
    not changed: a turn cost is a preference, not a delay.
    """
    expanded: nx.DiGraph[str] = nx.DiGraph()
    zero = dict.fromkeys(
        ("physical_length_m", "travel_time_s", "movement_time_s", "generalized_cost_s", *also),
        0.0,
    )
    for node in graph.nodes:
        expanded.add_node(node)
        expanded.add_node(sink(node), virtual=True)
        expanded.add_edge(node, sink(node), virtual=True, **zero)
    for u, v, attrs in graph.edges(data=True):
        here = state(u, v)
        expanded.add_node(here, virtual=True)
        expanded.add_edge(u, here, **attrs)
        expanded.add_edge(here, sink(v), virtual=True, **zero)
    clusters: dict[str, str] = dict(getattr(transition, "clusters", None) or {})
    through: Callable[[str, str, str, str], CostComponents] | None = getattr(
        transition, "through", None
    )
    if clusters and through is None:
        raise ValueError("a transition with clusters needs a through method")

    def charged(attrs: dict[str, object], costs: CostComponents, name: str) -> dict[str, object]:
        moved = dict(attrs)
        total = 0.0
        for field in MOVEMENT_FIELDS:
            added = float(getattr(costs, field))
            if not added >= 0.0:  # NaN fails too
                raise ValueError(f"movement {name} has an invalid {field}: {added}")
            if added:
                moved[field] = float(moved.get(field, 0.0)) + added  # type: ignore[arg-type]
                total += added
        moved["generalized_cost_s"] = float(attrs["generalized_cost_s"]) + total  # type: ignore[arg-type]
        for field in also:
            moved[field] = float(attrs[field]) + total  # type: ignore[arg-type]
        return moved

    for u, v in graph.edges:
        here = state(u, v)
        junction = clusters.get(v)
        entering = junction is not None and clusters.get(u) != junction
        if entering:
            _junction_states(expanded, graph, clusters, through, charged, u, v, zero)  # type: ignore[arg-type]
            continue
        for w, attrs in graph[v].items():
            if w == u:
                continue
            moved = charged(attrs, transition(u, v, w), f"{u}-{v}-{w}")
            expanded.add_edge(here, state(v, w), **moved)
    return expanded


def _junction_states(
    expanded: nx.DiGraph[str],
    graph: nx.DiGraph[str],
    clusters: dict[str, str],
    through: Callable[[str, str, str, str], CostComponents],
    charged: Callable[[dict[str, object], CostComponents, str], dict[str, object]],
    a: str,
    b: str,
    zero: dict[str, float],
) -> None:
    """The states of a route that entered a junction along ``a-b``, and its ways out."""
    junction = clusters[b]
    seen = {b}
    queue = [b]
    while queue:
        x = queue.pop()
        here = state(a, b) if x == b else inside(a, b, x)
        if x != b:
            expanded.add_node(here, virtual=True)
            expanded.add_edge(here, sink(x), virtual=True, **zero)
        for w, attrs in graph[x].items():
            if x == b and w == a:  # straight back out along the link it came in by
                continue
            if clusters.get(w) == junction:
                expanded.add_edge(here, state(a, b) if w == b else inside(a, b, w), **attrs)
                if w not in seen:
                    seen.add(w)
                    queue.append(w)
                continue
            moved = charged(attrs, through(a, b, x, w), f"{a}-{b}..{x}-{w}")
            expanded.add_edge(here, state(x, w), **moved)


def place(node: str) -> str:
    """The original node a movement-graph node stands at; a sink is a place of its own.

    For :func:`~walkbike_routes.routing.paths.multi_source_k_paths`, so that
    a route does not pass through the same street node twice.
    """
    if node.startswith(SINK):
        return node
    return node.rpartition(STATE)[2]  # also for a state inside a junction


def is_virtual(graph: nx.Graph[str], node: str) -> bool:
    """Whether ``node`` is a state or sink of a movement graph."""
    return bool(graph.nodes[node].get("virtual", False))


class Junctions:
    """Where ways meet, and how sharply a movement turns there.

    Every street counts towards a junction, so a road a mode may not use is
    still met.  Headings are measured over the ``HEADING_M`` of each link
    next to the node.
    """

    def __init__(
        self, graph: nx.DiGraph[str], physical_edges: gpd.GeoDataFrame, nodes: gpd.GeoDataFrame
    ) -> None:
        self.graph = graph
        self.points = dict(zip(nodes.node_id.astype(str), nodes.geometry, strict=True))
        unplaced = sorted(set(map(str, graph.nodes)) - set(self.points))
        if unplaced:
            raise ValueError(f"nodes missing from the node table: {unplaced[:5]}")
        ways: dict[str, set[str]] = {}
        for a, b in zip(physical_edges.u.astype(str), physical_edges.v.astype(str), strict=True):
            ways.setdefault(a, set()).add(b)
            ways.setdefault(b, set()).add(a)
        self._junctions = {node: len(found) >= 3 for node, found in ways.items()}
        self._headings: dict[tuple[str, str, str], float] = {}

    def junction(self, node: str) -> bool:
        """Whether three or more ways, usable or not, meet at ``node``."""
        return self._junctions.get(node, False)

    def heading_away(self, u: str, v: str, node: str) -> float:
        """Degrees anticlockwise from east, leaving ``node`` along link ``u-v``."""
        key = (u, v, node)
        if key not in self._headings:
            line = self.graph[u][v].get("geometry")
            if isinstance(line, MultiLineString):
                line = linemerge(line)  # connected parts become one line
            point = self.points.get(node)
            heading = math.nan
            if isinstance(line, LineString) and point is not None and line.length > 0:
                first, last = Point(line.coords[0]), Point(line.coords[-1])
                reach = min(HEADING_M, line.length)
                if first.distance(point) <= last.distance(point):
                    start, end = first, line.interpolate(reach)
                else:
                    start, end = last, line.interpolate(line.length - reach)
                if not start.equals(end):
                    heading = math.degrees(math.atan2(end.y - start.y, end.x - start.x))
            self._headings[key] = heading
        return self._headings[key]

    def change(self, u: str, v: str, w: str) -> float:
        """The change of heading moving u-v-w, in degrees: positive is left, NaN unknown."""
        arriving = self.heading_away(u, v, v) + 180.0
        leaving = self.heading_away(v, w, v)
        return (leaving - arriving + 180.0) % 360.0 - 180.0
