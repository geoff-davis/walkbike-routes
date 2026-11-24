"""Route searches on the part of the network near a destination, used only when provably exact.

A route that leaves a circle of radius ``R`` round the destination's entrances
covers at least the straight-line distance out to the circle and back, and no
link costs less per metre of straight line than the network's cheapest
(:func:`cost_per_metre`).  So for a node at straight-line distance ``d`` from
the nearest entrance, every route through a node outside the circle costs at
least ``per_m * max(2R - d, d)`` (:meth:`Near.bound`).  Below that bound,
searches on the cut network and on the whole network find the same costs and
the same paths, in the same order: the bound changes by at most one link's
cost per link, so a label cheaper than it is only ever built from labels
cheaper than theirs.

:func:`exact_homes` and :func:`exact_alternatives` check a run against the
bound; :func:`radius_needed` says how wide a circle would pass.  Costs are in
the search weight's units.
"""

from __future__ import annotations

import math
import weakref
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import networkx as nx
import numpy as np
import pandas as pd

from walkbike_routes.routing.turns import SINK, STATE

_PER_M: weakref.WeakKeyDictionary[nx.Graph[str], dict[str, float]] = weakref.WeakKeyDictionary()
MARGIN = 1.02  # widen a little past the radius a check asks for


def at(node: str) -> str:
    """The street node a search-graph node stands at (itself on a plain graph)."""
    if node.startswith(SINK):
        return node[len(SINK) :]
    return node.rpartition(STATE)[2]


def cost_per_metre(
    graph: nx.Graph[str], xy: Mapping[str, tuple[float, float]], weight: str
) -> float:
    """The least cost of any link per metre of straight line between its ends.

    0 when a link with a length costs nothing, and NaN when a link costs less
    than nothing or an end has no position: no bound can be drawn then.
    Remembered per graph and weight.
    """
    known = _PER_M.setdefault(graph, {})
    if weight in known:
        return known[weight]
    least = math.inf
    for u, v, data in graph.edges(data=True):
        cost = float(data.get(weight, math.nan))
        a, b = xy.get(at(str(u))), xy.get(at(str(v)))
        if not cost >= 0 or a is None or b is None:
            least = math.nan
            break
        metres = math.hypot(a[0] - b[0], a[1] - b[1])
        if metres > 0:
            least = min(least, cost / metres)
    known[weight] = least
    return least


@dataclass(frozen=True)
class Near:
    """The network within ``radius_m`` of the entrances, and what bounds its costs."""

    graph: nx.Graph[str]
    radius_m: float
    per_m: float
    away_m: Mapping[str, float]  # street node -> straight-line metres to the nearest entrance

    def bound(self, node: str) -> float:
        """Every route to ``node`` through a node cut away costs at least this."""
        d = self.away_m.get(at(node), math.inf)
        return self.per_m * max(2 * self.radius_m - d, d)


def distances(xy: Mapping[str, tuple[float, float]], entrances: Iterable[str]) -> dict[str, float]:
    """Straight-line metres from every street node to the nearest of ``entrances``."""
    ends = np.array([xy[e] for e in entrances if e in xy], dtype=float).reshape(-1, 2)
    names = list(xy)
    if not len(ends) or not names:
        return dict.fromkeys(names, math.inf)
    points = np.array([xy[n] for n in names], dtype=float)
    best = np.full(len(names), np.inf)
    for x, y in ends:
        best = np.minimum(best, np.hypot(points[:, 0] - x, points[:, 1] - y))
    return dict(zip(names, best.tolist(), strict=True))


def near(
    graph: nx.Graph[str],
    away_m: Mapping[str, float],
    per_m: float,
    radius_m: float,
) -> Near | None:
    """The part of ``graph`` within ``radius_m`` of the entrances, or None if no part is cut."""
    if not (per_m > 0 and math.isfinite(per_m)):
        return None
    keep = [n for n in graph if away_m.get(at(str(n)), math.inf) <= radius_m]
    if len(keep) == graph.number_of_nodes():
        return None
    return Near(graph.subgraph(keep).copy(), radius_m, per_m, away_m)


def reaches(graph: nx.Graph[str], targets: Iterable[str]) -> set[str]:
    """The nodes of ``graph`` from which some target can be reached."""
    seen = {t for t in targets if t in graph}
    stack = list(seen)
    back = graph.pred if graph.is_directed() else graph.adj  # type: ignore[attr-defined]
    while stack:
        for previous in back[stack.pop()]:
            if previous not in seen:
                seen.add(previous)
                stack.append(previous)
    return seen


def exact_homes(
    cut: Near,
    cost_by_node: Mapping[str, float],
    ends: Iterable[str],
    reachable: set[str] | None,
) -> tuple[bool, float]:
    """Whether every home's best route on the cut network is its best on the whole.

    ``ends`` are the street nodes at the ends of the homes' streets; a home's
    route is the same if both of its ends cost the same.  An end is safe if it
    costs less than its bound, or if it reaches no entrance at all on the whole
    network (``reachable``, from :func:`reaches`; None when not yet known, and
    then an unreachable end fails).  Also returns the radius the failing ends
    ask for (infinite when an end cannot say).
    """
    needed = 0.0
    for end in set(ends):
        cost = cost_by_node.get(end)
        if cost is not None and cost < cut.bound(end):
            continue
        if cost is None and reachable is not None and end not in reachable:
            continue
        needed = max(needed, radius_needed(cut, end, cost))
    return needed == 0.0, needed


def exact_alternatives(
    cut: Near, best_by_node: Mapping[str, float], gap: float
) -> tuple[bool, float]:
    """Whether each node's near-best routes on the cut network are those on the whole.

    A node keeps routes within ``gap`` of its best, so they are all found
    below the bound when ``best + gap`` is.  Also returns the radius asked for.
    """
    needed = 0.0
    for node, best in best_by_node.items():
        if best + gap >= cut.bound(node):
            needed = max(needed, radius_needed(cut, node, best + gap))
    return needed == 0.0, needed


def radius_needed(cut: Near, node: str, cost: float | None) -> float:
    """A radius whose bound at ``node`` is above ``cost`` (infinite if unknown)."""
    d = cut.away_m.get(at(node), math.inf)
    if cost is None or not math.isfinite(cost) or not math.isfinite(d):
        return math.inf
    return max((cost / cut.per_m + d) / 2, d) * MARGIN + 1.0


def home_ends(attachments: pd.DataFrame, origin_ids: Iterable[str]) -> list[str]:
    """The street nodes each of these homes may start from: both ends of its street,
    and the node it is attached at (used when its street is not known)."""
    rows = attachments[attachments.origin_id.astype(str).isin(set(map(str, origin_ids)))]
    rows = rows[rows.network_attached.astype(bool)]
    columns = [c for c in ("edge_u", "edge_v", "attached_node_id") if c in rows]
    return [str(n) for col in columns for n in rows[col].dropna()]
