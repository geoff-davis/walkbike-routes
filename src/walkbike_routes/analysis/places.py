"""Names and shapes of places on the street network, shared by the screens and the exposure lists.

A junction drawn as several nodes is one place; a mapped crossing made of
several pieces is one place; a run of links is named by its street and the
streets it meets.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping

import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely.geometry import Point
from shapely.ops import unary_union

JUNCTION = "J:"  # the intervention key of a junction treatment: J:<node>


JUNCTION_JOIN_M = 60.0  # nodes of one junction (a divided road's two carriageways)


def crossing_ways(edges: gpd.GeoDataFrame) -> pd.Series:
    """Mapped crossings: links that cross a road rather than run along one."""
    blank = pd.Series("", index=edges.index)
    crossed = edges.get("crossed_road", blank).fillna("").astype(str).str.strip().ne("")
    across = edges.get("movement_type", blank).fillna("").astype(str).eq("across")
    return crossed | across


def edge_names(edges: gpd.GeoDataFrame) -> dict[str, str]:
    """Street names by physical edge, or the road type for unnamed ones."""
    blank = pd.Series("", index=edges.index)
    names = edges.get("name", blank).fillna("").astype(str).str.strip()
    kinds = edges.get("highway", blank).fillna("").astype(str).str.replace("_", " ")
    labels = [
        name or (f"unnamed {kind}" if kind else "unnamed link")
        for name, kind in zip(names, kinds, strict=True)
    ]
    return dict(zip(edges.physical_edge_id.astype(str), labels, strict=True))


def way_of(physical_edge_id: str) -> str:
    """The mapped way a physical edge is a piece of (``w123-0`` is a piece of ``w123``)."""
    return re.sub(r"-\d+$", "", physical_edge_id)


def same_junction(places: Places, a: str, b: str) -> bool:
    """Whether nodes ``a`` and ``b`` are one junction drawn as several nodes.

    They are within JUNCTION_JOIN_M, and either share two street names, or
    share one (a divided road) and are joined by a link of another name or
    none: the gap between its carriageways.  Two side streets a few metres
    apart along one road are joined by that road, so they stay two junctions.
    """
    pa, pb = places.points.get(a), places.points.get(b)
    if a == b or pa is None or pb is None or pa.distance(pb) > JUNCTION_JOIN_M:
        return a == b
    shared = places.streets_at.get(a, set()) & places.streets_at.get(b, set())
    if len(shared) >= 2:
        return True
    between = places.links.get(frozenset((a, b)), set())
    return bool(shared) and any(name not in shared for name in between)


def junction_groups(places: Places, flow: Mapping[str, float]) -> list[tuple[str, list[str]]]:
    """Junction keys with flow; the nodes of one junction (:func:`same_junction`) are one.

    Groups are connected components, so the order nodes come in does not matter.
    Each group is listed with its keys busiest first, the busiest groups first,
    and is labelled by its busiest node.
    """
    keys = sorted(
        (k for k, value in flow.items() if k.startswith(JUNCTION) and value > 0),
        key=lambda k: (-flow[k], k),
    )
    label = {k: places.junction(k[len(JUNCTION) :]) for k in keys}
    point = {k: places.points.get(k[len(JUNCTION) :]) for k in keys}
    joined: nx.Graph[str] = nx.Graph()
    joined.add_nodes_from(keys)
    for n, a in enumerate(keys):
        for b in keys[n + 1 :]:
            pa, pb = point[a], point[b]
            if pa is None or pb is None or pa.distance(pb) > JUNCTION_JOIN_M:
                continue
            if label[a] == label[b] or same_junction(
                places, a[len(JUNCTION) :], b[len(JUNCTION) :]
            ):
                joined.add_edge(a, b)
    rank = {k: n for n, k in enumerate(keys)}
    groups = [sorted(c, key=rank.__getitem__) for c in nx.connected_components(joined)]
    groups.sort(key=lambda g: rank[g[0]])
    return [(label[g[0]], g) for g in groups]


class Places:
    """Names and shapes for crossing and junction candidates."""

    def __init__(self, edges: gpd.GeoDataFrame) -> None:
        crossing = crossing_ways(edges)
        named = edges.get("name", pd.Series("", index=edges.index)).fillna("").astype(str)
        blank = pd.Series("", index=edges.index)
        ends_u = edges.get("u", blank).astype(str)  # a made-up network may lack node ids
        ends_v = edges.get("v", blank).astype(str)
        self.lines = dict(zip(edges.physical_edge_id.astype(str), edges.geometry, strict=True))
        # Which crossing each piece belongs to: pieces of one way, or meeting on the
        # road crossed, share one crossing (``crossing_group``) and split its cost.
        group = edges.get("crossing_group", blank).fillna("").astype(str)
        self.crossing_of = {
            pid: g or way_of(pid)
            for pid, g in zip(edges.physical_edge_id.astype(str), group, strict=True)
        }
        self.ends: dict[str, tuple[str, str]] = dict(
            zip(edges.physical_edge_id.astype(str), zip(ends_u, ends_v, strict=True), strict=True)
        )  # fmt: skip
        self.streets_at: defaultdict[str, set[str]] = defaultdict(set)
        self.links: defaultdict[frozenset[str], set[str]] = defaultdict(set)  # names between
        self.points: dict[str, Point] = {}
        for u, v, name, across, line in zip(
            ends_u, ends_v, named.str.strip(), crossing,
            edges.geometry, strict=True,
        ):  # fmt: skip
            parts = list(getattr(line, "geoms", [line]))  # a multipart edge has no coords
            self.points.setdefault(u, Point(parts[0].coords[0]))
            self.points.setdefault(v, Point(parts[-1].coords[-1]))
            self.links[frozenset((u, v))].add(name)
            if name and not across:
                self.streets_at[u].add(name)
                self.streets_at[v].add(name)
        keep = (~crossing & named.str.strip().ne("")).to_numpy()
        self.streets = gpd.GeoDataFrame(
            {"name": named.str.strip().to_numpy()[keep]},
            geometry=edges.geometry.to_numpy()[keep],
            crs=edges.crs,
        )

    def junction(self, node: str) -> str:
        found = sorted(self.streets_at.get(node, set()))
        if not found:
            near = self._nearest(self.points[node], set()) if node in self.points else ""
            return f"Junction near {near}" if near else "Junction"
        if len(found) == 1:  # one street meets itself here: say which cross street is near
            near = self._nearest(self.points[node], set(found)) if node in self.points else ""
            return f"{found[0]} near {near}" if near else found[0]
        return ", ".join(found[:-1]) + " and " + found[-1]

    def crossing(self, ids: Iterable[str]) -> str:
        nodes = {n for p in ids for n in self.ends.get(p, ())}
        crossed = sorted({s for n in nodes for s in self.streets_at.get(n, set())})
        line = unary_union([self.lines[p] for p in ids])
        label = "Crossing of " + " and ".join(crossed) if crossed else "Crossing"
        near = self._nearest(line.centroid, set(crossed))
        return f"{label} near {near}" if near else label

    def length(self, ids: Iterable[str]) -> float:
        return float(sum(self.lines[p].length for p in ids))

    def _nearest(self, point: Point, besides: set[str], within_m: float = 80.0) -> str:
        others = self.streets[~self.streets.name.isin(besides)]
        if others.empty:
            return ""
        distance = others.distance(point).to_numpy(dtype=float)
        best = int(distance.argmin())
        return str(others.name.iloc[best]) if distance[best] <= within_m else ""

    def point(self, key: str) -> Point:
        return self.points[key[len(JUNCTION) :]]


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, item: str) -> None:
        self.parent.setdefault(item, item)

    def find(self, item: str) -> str:
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[max(left_root, right_root)] = min(left_root, right_root)


def section_label(name: str, cross_streets: list[str]) -> str:
    """Describe a section by its street and the comfortable streets it meets."""
    street = name or "Unnamed link"
    if len(cross_streets) >= 2:
        return f"{street}: {cross_streets[0]} to {cross_streets[1]}"
    if cross_streets:
        return f"{street} near {cross_streets[0]}"
    return street
