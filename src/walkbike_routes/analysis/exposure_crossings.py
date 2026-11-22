"""Exposure at crossings: where each route crosses a road, and each crossing place's points."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
from shapely.ops import linemerge, unary_union

from walkbike_routes.analysis.exposure_lists import (
    CROSSING_COLUMNS,
    by_source,
    factors_of,
    has_count,
    number_of,
    ranked,
    text_of,
    traffic_words,
)
from walkbike_routes.analysis.exposure_rules import (
    ExposureRules,
    RiskPoints,
    signal_words,
    tidy,
)
from walkbike_routes.analysis.places import (
    JUNCTION,
    Places,
    crossing_ways,
    junction_groups,
    same_junction,
)
from walkbike_routes.network.broach import traffic_band
from walkbike_routes.network.broach_walk import signalised_crossing

# Risk points and the fix-first rule are settings (analysis/exposure_rules.py),
# hand-set by default and changeable under ``exposure`` in a study's config.
NEAR_ROAD_M = 30.0  # a mapped crossing takes its road's details from within this


LEFT_TURN_DEG = 30.0  # a signed turn over this is a left turn (right-hand traffic)


STRAIGHT_ON_DEG = 30.0  # two legs this close to opposite ways are one road line


@dataclass(frozen=True)
class _Edge:
    u: str
    v: str
    name: str
    motor: bool
    band: int
    speed: float
    lanes: float
    highway: str
    oneway: bool
    reverse: bool  # one way against the drawn direction (oneway=-1)
    counted: bool  # traffic from a count, not the road type
    line: Any


def _bearing_from(line: Any, node: Point, *, from_end: bool = False) -> float:
    """The direction, in degrees counter-clockwise from east, leaving ``node`` along ``line``.

    ``from_end`` takes the line's last end, for a loop that starts and ends at ``node``.
    """
    if hasattr(line, "geoms"):  # join connected pieces, some may be drawn reversed
        line = linemerge(line)
    parts = list(getattr(line, "geoms", [line]))
    first, last = parts[0].coords, parts[-1].coords
    if not from_end and Point(first[0]).distance(node) <= Point(last[-1]).distance(node):
        coords = list(first)
    else:
        coords = list(reversed(list(last)))
    x0, y0 = coords[0][:2]
    for x, y in (c[:2] for c in coords[1:]):
        if (x, y) != (x0, y0):
            return math.degrees(math.atan2(y - y0, x - x0))
    return math.nan


def _turn(arrive_from: float, leave_to: float) -> float:
    """Signed turn in degrees (left positive) for travel arriving from one leg, leaving by another."""
    heading_in = arrive_from + 180.0
    return (leave_to - heading_in + 180.0) % 360.0 - 180.0


def _between(x: float, start: float, end: float) -> bool:
    """Whether direction ``x`` lies on the counter-clockwise arc from ``start`` to ``end``."""
    return (x - start) % 360.0 < (end - start) % 360.0


def _angle(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


class _Network:
    def __init__(self, edges: gpd.GeoDataFrame, nodes: gpd.GeoDataFrame) -> None:
        self.edges: dict[str, _Edge] = {}
        self.crosses: dict[str, str] = {}  # crossing way -> road class it crosses
        self.at: defaultdict[str, list[str]] = defaultdict(list)
        for record, line in zip(
            edges.drop(columns="geometry").to_dict("records"), edges.geometry, strict=True
        ):
            pid = str(record["physical_edge_id"])
            band = traffic_band(record.get("highway"), record.get("lanes"),
                                record.get("traffic_aadt"))  # fmt: skip
            edge = _Edge(
                u=str(record.get("u")), v=str(record.get("v")), name=text_of(record.get("name")),
                motor=bool(record.get("car_allowed", False)) and band >= 0, band=band,
                speed=number_of(record.get("speed_mph")), lanes=number_of(record.get("lanes")),
                highway=text_of(record.get("highway")).lower(),
                oneway=text_of(record.get("oneway")).lower() in {"yes", "-1", "true", "1"},
                reverse=text_of(record.get("oneway")).lower() == "-1",
                counted=has_count(record.get("traffic_aadt")),
                line=line,
            )  # fmt: skip
            self.edges[pid] = edge
            road = text_of(record.get("crossed_road")) or text_of(record.get("crossing_road"))
            if road and (
                text_of(record.get("crossed_road"))
                or text_of(record.get("movement_type")) == "across"
            ):
                self.crosses[pid] = road.lower()
            self.at[edge.u].append(pid)
            if edge.v != edge.u:
                self.at[edge.v].append(pid)
        frame = nodes.set_index(nodes.node_id.astype(str))
        self.points: dict[str, Point] = {
            str(k): Point(g.coords[0]) for k, g in zip(frame.index, frame.geometry, strict=True)
        }
        self.control = frame.get("control", pd.Series("", index=frame.index)).fillna("")
        self.marking = frame.get("crossing_marking", pd.Series("", index=frame.index)).fillna("")
        self._roads: dict[str, list[tuple[str, list[str]]]] = {}

    def roads_through(self, node: str) -> list[tuple[str, list[str]]]:
        """Named motor roads of band 1+ at ``node``, with their legs there.

        A road that ends here has one leg: it can be turned onto or off, not crossed
        straight over."""
        if node not in self._roads:
            legs: defaultdict[str, list[str]] = defaultdict(list)
            for pid in self.at.get(node, []):
                edge = self.edges[pid]
                if edge.motor and edge.name:
                    legs[edge.name].append(pid)
            self._roads[node] = [
                (name, pids)
                for name, pids in sorted(self._one_road(node, legs).items())
                if max(self.edges[p].band for p in pids) >= 1
            ]
        return self._roads[node]

    def motor_legs(self, node: str) -> int:
        """The motor legs at ``node``; a loop leaves and comes back, so it is two."""
        return sum(
            2 if self.edges[p].u == self.edges[p].v else 1
            for p in self.at.get(node, [])
            if self.edges[p].motor
        )

    def _one_road(self, node: str, legs: Mapping[str, list[str]]) -> dict[str, list[str]]:
        """``legs`` with a road that changes name at ``node`` joined into one road.

        Two roads that each have one leg here, pointing within STRAIGHT_ON_DEG of
        opposite ways, are one road line under two names, and it is called by
        both ("East Main / West Main"), so every node along it names it alike.
        Each leg joins its most nearly opposite partner.  A loop is two legs of
        its own road already.
        """
        single = {
            name: pids[0]
            for name, pids in legs.items()
            if len(pids) == 1
            and self.edges[pids[0]].u != self.edges[pids[0]].v
            and not math.isnan(self.bearing(pids[0], node))
        }
        pairs = sorted(
            (off, a, b)
            for a in single
            for b in single
            if a < b
            and (off := 180.0 - _angle(self.bearing(single[a], node),
                                       self.bearing(single[b], node))) <= STRAIGHT_ON_DEG
        )  # fmt: skip
        joined = {name: list(pids) for name, pids in legs.items()}
        used: set[str] = set()
        for _, a, b in pairs:
            if a in used or b in used:
                continue
            used |= {a, b}
            del joined[a], joined[b]
            joined[f"{a} / {b}"] = [single[a], single[b]]
        return joined

    def bearing(self, pid: str, node: str, *, arriving: bool = False) -> float:
        """The leg's direction out of ``node``.  A loop (u == v) is taken as drawn,
        u to v: arriving at its node by its last end, leaving by its first."""
        edge = self.edges[pid]
        loop = edge.u == edge.v
        return _bearing_from(edge.line, self.points[node], from_end=loop and arriving)


def _crosses(net: _Network, a: str, b: str, node: str, road: str, legs: list[str]) -> bool:
    """Whether travel from link ``a`` to link ``b`` at ``node`` crosses ``road``."""
    on_a = a in legs or net.edges[a].name == road
    on_b = b in legs or net.edges[b].name == road
    if on_a and on_b:
        return False
    came, going = net.bearing(a, node, arriving=True), net.bearing(b, node)
    if math.isnan(came) or math.isnan(going):
        return False
    if on_a or on_b:
        # Turning onto or off the road: only a left turn crosses it, and only at a
        # junction.  Where two roads meet end to end (a bend) nothing is crossed.
        return net.motor_legs(node) >= 3 and _turn(came, going) > LEFT_TURN_DEG
    line = _road_line(net, node, legs)
    if line is None:
        return False
    r1, r2 = line
    return _between(came, r1, r2) != _between(going, r1, r2)


def _only_road_of(net: _Network, node: str, highway: str) -> str | None:
    """The one road of class ``highway`` through ``node``; None when none or several."""
    found = [
        road
        for road, legs in net.roads_through(node)
        if any(net.edges[leg].highway == highway for leg in legs)
    ]
    return found[0] if len(found) == 1 else None


def _road_line(net: _Network, node: str, legs: list[str]) -> tuple[float, float] | None:
    """The road's line at ``node``: its two legs that point most nearly opposite ways."""
    bearings = [deg for p in legs if not math.isnan(deg := net.bearing(p, node))]
    if len(bearings) < 2:
        return None
    r1, r2 = max(
        ((x, y) for i, x in enumerate(bearings) for y in bearings[i + 1 :]),
        key=lambda pair: abs((pair[0] - pair[1] + 180.0) % 360.0 - 180.0),
    )
    if abs((r1 - r2 + 180.0) % 360.0 - 180.0) < 1.0:  # all legs leave the same way
        return None
    return r1, r2


def _spans(net: _Network, pieces: list[str], node: str, legs: list[str]) -> bool:
    """Whether a mapped crossing's pieces reach both sides of the road through ``node``."""
    line = _road_line(net, node, legs)
    if line is None:
        return False
    here = net.points[node]
    sides = set()
    for pid in pieces:
        edge = net.edges.get(pid)
        if edge is None:
            continue
        for end in (edge.u, edge.v):
            point = net.points.get(end)
            if point is None or point.distance(here) < 1.0:
                continue
            deg = math.degrees(math.atan2(point.y - here.y, point.x - here.x))
            sides.add(_between(deg, *line))
    return len(sides) == 2


def _shared_node(net: _Network, a: str, b: str) -> str | None:
    ea, eb = net.edges.get(a), net.edges.get(b)
    if ea is None or eb is None:
        return None
    common = {ea.u, ea.v} & {eb.u, eb.v}
    return next(iter(common)) if len(common) == 1 else None


def crossing_events(
    shares: pd.DataFrame,
    edges: gpd.GeoDataFrame,
    nodes: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Each route's crossings: one row per route and crossing place.

    A route crosses at a mapped crossing way (keyed by its crossing group), or
    at a junction (``J:<node>``) where it goes straight across a named road of
    band 1+ or turns left onto or off it.  Each route counts once per place.
    """
    net = _Network(edges, nodes)
    places = Places(edges)
    across = set(edges.physical_edge_id[crossing_ways(edges)].astype(str))
    groups: defaultdict[str, list[str]] = defaultdict(list)
    for pid in edges.physical_edge_id.astype(str):
        groups[places.crossing_of.get(pid, pid)].append(pid)
    rows: list[dict[str, object]] = []
    for route, (ids, weight) in enumerate(
        zip(shares.physical_edge_ids, shares.assigned_weight, strict=True)
    ):
        path = [p for p in str(ids).split(";") if p]
        seen: dict[tuple[str, str], str] = {}  # (place, detail) -> kind
        for pid in path:
            group = places.crossing_of.get(pid, pid)
            if pid in across and not any(k[0] == group for k in seen):
                seen[(group, pid)] = "way"
        for a, b, node in _turns(net, path):
            # A mapped crossing that passes over a road at this node is counted once,
            # as the crossing; another road there is still a junction crossing.
            ways = [p for p in (a, b) if p in net.crosses]
            for road, legs in net.roads_through(node):
                if any(_spans(net, groups[places.crossing_of.get(p, p)], node, legs)
                       for p in ways):  # fmt: skip
                    continue
                # A crossing way that ends on the road it crosses, with the route then
                # going along that road, is the crossing: the turn is not a second one.
                # Where two roads of the way's class meet, it may cross either: both count.
                if (a in legs or b in legs) and any(
                    _only_road_of(net, node, net.crosses[p]) == road for p in ways
                ):
                    continue
                if _crosses(net, a, b, node, road, legs):
                    seen[(JUNCTION + node, road)] = "junction"
        for (key, detail), kind in seen.items():
            rows.append(
                {"route": route, "place": key, "kind": kind, "detail": detail,
                 "weight": float(weight)}
            )  # fmt: skip
    return pd.DataFrame(rows, columns=["route", "place", "kind", "detail", "weight"])


def _turns(net: _Network, path: list[str]) -> list[tuple[str, str, str]]:
    """Consecutive links of a route with the node between them, in travel order.

    The route is followed end to end, so links sharing both ends (a pair of
    one-way links, or parallel links) still give the node actually passed.
    """
    known = [p for p in path if p in net.edges]
    if len(known) < 2:
        return []
    first = net.edges[known[0]]
    walks = [_walk(net, known, start) for start in dict.fromkeys((first.u, first.v))]
    return max(walks, key=len)  # the start that follows the route furthest


def _walk(net: _Network, known: list[str], here: str) -> list[tuple[str, str, str]]:
    turns: list[tuple[str, str, str]] = []
    for a, b in zip(known, known[1:], strict=False):
        edge = net.edges[a]
        if here not in (edge.u, edge.v):  # a gap: fall back to the shared end
            node = _shared_node(net, a, b)
            if node is None:
                here = ""
                continue
        else:
            node = edge.v if here == edge.u else edge.u
        if node not in (net.edges[b].u, net.edges[b].v):
            return turns  # this start does not follow the route
        turns.append((a, b, node))
        here = node
    return turns


def _crossing_points(
    band: int,
    speed: float,
    lanes: float,
    *,
    mode: str,
    unmarked: bool,
    signal: bool,
    counted: bool = True,
    points: RiskPoints | None = None,
) -> tuple[float, str]:
    points = points or RiskPoints()
    band = min(max(band, 0), 4)
    parts = [(traffic_words(band, counted), float(points.crossing_band[band]))]
    if speed >= points.fast_mph:
        parts.append((f"{points.fast_mph:.12g} mph or faster", points.fast))
    if lanes >= points.wide_lanes:
        parts.append((f"{points.wide_lanes:.12g} or more lanes to cross", points.wide))
    if mode == "walk" and unmarked and band >= 1:
        parts.append(("no marked crosswalk on the map", points.unmarked))
    total, text = factors_of(parts)
    if signal and total:
        total = tidy(total * points.signal_factor)
        text += f"; has a signal ({signal_words(points.signal_factor)})"
    return total, text


def exposed_crossings(
    events: pd.DataFrame,
    edges: gpd.GeoDataFrame,
    nodes: gpd.GeoDataFrame,
    mode: str,
    counted: float,
    signal_legs: frozenset[str] = frozenset(),
    signal_nodes: frozenset[str] = frozenset(),
    rules: ExposureRules | None = None,
    flow_noun: str = "people",
) -> gpd.GeoDataFrame:
    """Crossing places ranked by the people crossing there × the place's points.

    ``signal_nodes`` are the nodes inside a signal's footprint (the signal
    inventory's clusters), so a signal mapped on a junction's approaches still
    controls the junction.
    """
    rules = rules or ExposureRules()
    if events.empty:
        return ranked(pd.DataFrame(columns=CROSSING_COLUMNS[1:]), CROSSING_COLUMNS, edges.crs,
                       counted, rules.fix_first, flow_noun)  # fmt: skip
    net = _Network(edges, nodes)
    places = Places(edges)
    records = {
        str(r["physical_edge_id"]): {str(k): v for k, v in r.items()}
        for r in edges.drop(columns="geometry").to_dict("records")
    }
    roads = edges[[e.motor for e in net.edges.values()]]
    people = events.drop_duplicates(["route", "place"]).groupby("place").weight.sum()
    rows = []
    junctions = {str(p): float(w) for p, w in people.items() if str(p).startswith(JUNCTION)}
    signals = [str(n) for n, c in net.control.items() if str(c) == "signal"]
    signals += sorted(set(signal_nodes) - set(signals))
    for label, keys in junction_groups(places, junctions):
        # One signal controls the whole junction, even when it is mapped at one node.
        signal = any(_signalled(net, places, k[len(JUNCTION) :], signals) for k in keys)
        divided = _carriageway_lanes(net, keys, events)
        scored = [
            _junction_points(net, k[len(JUNCTION) :], events, mode, signal, divided, rules.points)
            for k in keys
        ]
        points, text = max(scored, key=lambda s: s[0])
        # A route through both nodes of one junction (a divided road) counts once.
        once = events[events.place.isin(keys)].drop_duplicates("route")
        crossing = float(once.weight.sum())
        rows.append(
            {
                "place_id": keys[0], "label": label, "people": crossing, "risk_points": points,
                "risk_factors": text, "exposure": crossing * points,
                "people_by_destination": by_source(once), "physical_edge_ids": ";".join(keys),
                "geometry": net.points.get(keys[0][len(JUNCTION) :])
                or places.point(keys[0]),
            }
        )  # fmt: skip
    ways = events[events.kind.eq("way")]
    members: defaultdict[str, list[str]] = defaultdict(list)
    for pid in edges.physical_edge_id[crossing_ways(edges)].astype(str):
        members[places.crossing_of.get(pid, pid)].append(pid)
    for place, group in ways.groupby("place"):
        pids = sorted(members.get(str(place)) or set(group.detail.astype(str)))
        record = records[pids[0]]
        line = unary_union([places.lines[p] for p in pids])
        road_class = text_of(record.get("crossed_road")) or text_of(record.get("crossing_road"))
        near = roads[roads.highway.astype(str).eq(road_class)] if road_class else roads.iloc[:0]
        near = near[near.distance(line.centroid) <= NEAR_ROAD_M]
        if len(near):
            road = near.iloc[int(near.distance(line.centroid).to_numpy().argmin())]
            band = traffic_band(road.get("highway"), road.get("lanes"), road.get("traffic_aadt"))
            speed, lanes = number_of(road.get("speed_mph")), number_of(road.get("lanes"))
            counted_road = has_count(road.get("traffic_aadt"))
        else:
            band, speed, lanes = traffic_band(road_class, None), math.nan, math.nan
            counted_road = False
        marking = text_of(record.get("crossing_marking")).lower()
        unmarked = marking == "unmarked" or text_of(record.get("crossing")).lower() in {
            "unmarked",
            "no",
        }
        signal = any(signalised_crossing(records[p], signal_legs) for p in pids)
        points, text = _crossing_points(band, speed, lanes, mode=mode, unmarked=unmarked,
                                        signal=signal, counted=counted_road,
                                        points=rules.points)  # fmt: skip
        crossing = float(group.weight.sum())
        rows.append(
            {
                "place_id": f"X:{place}", "label": places.crossing(pids), "people": crossing,
                "risk_points": points, "risk_factors": text, "exposure": crossing * points,
                "people_by_destination": by_source(group), "physical_edge_ids": ";".join(pids),
                "geometry": line.centroid,
            }
        )  # fmt: skip
    return ranked(pd.DataFrame(rows, columns=CROSSING_COLUMNS[1:]), CROSSING_COLUMNS, edges.crs,
                   counted, rules.fix_first, flow_noun)  # fmt: skip


def _signalled(net: _Network, places: Places, node: str, signals: list[str]) -> bool:
    """A signal at ``node``, or at another node of the same junction, as junctions are grouped."""
    if str(net.control.get(node, "")) == "signal" or node in signals:
        return True
    return any(same_junction(places, node, other) for other in signals)


def _carriageway_lanes(net: _Network, keys: list[str], events: pd.DataFrame) -> dict[str, float]:
    """Lanes of a divided road crossed at a junction: its one-way carriageways added up.

    Each carriageway of a divided road is mapped with its own lanes, at its own
    node.  Where a junction's nodes cross a road only on one-way legs, the lanes
    of each carriageway are summed.  A carriageway (one direction of travel)
    counts once, though it may pass several of the junction's nodes, as where
    two divided roads meet.
    """
    at: defaultdict[str, list[tuple[float, float]]] = defaultdict(list)  # (heading, lanes)
    for key in keys:
        node = key[len(JUNCTION) :]
        for road in set(events.detail[events.place.eq(key)].astype(str)):
            legs = [p for name, pids in net.roads_through(node) if name == road for p in pids]
            if not legs or not all(net.edges[p].oneway for p in legs):
                continue
            # A road's one-way legs at one node are one carriageway, even where it
            # bends; carriageways are matched across nodes by direction of travel.
            headings = [h for p in legs if not math.isnan(h := _travel_heading(net, p, node))]
            finite = [n for p in legs if math.isfinite(n := net.edges[p].lanes)]
            if not headings or not finite:
                continue
            heading, lanes = headings[0], max(finite)
            ways = at[road]
            same = [i for i, (h, _) in enumerate(ways) if _angle(h, heading) < 45.0]
            if same:
                h, n = ways[same[0]]
                ways[same[0]] = (h, max(n, lanes))
            else:
                ways.append((heading, lanes))
    return {road: sum(n for _, n in ways) for road, ways in at.items() if len(ways) >= 2}


def _travel_heading(net: _Network, pid: str, node: str) -> float:
    """The direction traffic moves along a one-way link, where it meets ``node``."""
    edge = net.edges[pid]
    if node not in net.points:
        return math.nan
    out = net.bearing(pid, node)  # pointing away from the node
    leaving = (node == edge.u) != edge.reverse  # traffic moves away from the node
    return out if leaving else (out + 180.0) % 360.0


def _junction_points(
    net: _Network,
    node: str,
    events: pd.DataFrame,
    mode: str,
    signal: bool,
    road_lanes: dict[str, float] | None = None,
    points: RiskPoints | None = None,
) -> tuple[float, str]:
    crossed = events[events.place.eq(JUNCTION + node)].detail.astype(str)
    best = (0.0, "")
    for road in sorted(set(crossed)):
        legs = [p for name, pids in net.roads_through(node) if name == road for p in pids]
        if not legs:
            continue
        band = max(net.edges[p].band for p in legs)
        speed = max((s for p in legs if math.isfinite(s := net.edges[p].speed)), default=math.nan)
        lanes = max((n for p in legs if math.isfinite(n := net.edges[p].lanes)), default=math.nan)
        lanes = (road_lanes or {}).get(road, lanes)
        unmarked = str(net.marking.get(node, "")) != "marked"
        counted = any(net.edges[p].counted for p in legs if net.edges[p].band == band)
        scored = _crossing_points(
            band,
            speed,
            lanes,
            mode=mode,
            unmarked=unmarked,
            signal=signal,
            counted=counted,
            points=points,
        )
        if scored[0] > best[0]:
            best = (scored[0], f"crossing {road}: {scored[1]}")
    return best
