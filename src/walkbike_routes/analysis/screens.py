"""Improvement screens: which street sections, if fixed, would save the most.

Each treatment is screened on its own.  It is applied to the prepared streets
(or, at junctions, to the preset's movement costs) and the mode's links are
rebuilt with the same preset, so the improved cost comes from the same model
as the baseline:

- protected bike lane (cycling): a street at traffic stress 3 or 4 rides
  like stress 1.  Streets with 10,000 or more vehicles a day and no protected
  lane get one too, whatever their stress level: Broach's model charges for
  that traffic even where a 25 mph limit makes the street LTS 2;
- sidewalk (walking): a street link with missing or poor pedestrian evidence
  counts as supported.  Links the preset leaves out today (for example a busy
  road with no sidewalk) can be opened;
- marked crosswalk (walking): a mapped crossing gets paint, and so does a
  junction where the walking model charges an unmarked crossing of a
  collector street;
- signal or crossing beacon (walking and cycling): an unsignalised crossing
  of a busy road gets a signal.  The crossing penalty goes, and the signal's
  own cost comes in: the walking signal wait, or the cycling model's signal
  term.

Streets a street treatment would improve are grouped into same-street
sections; crossing treatments are tried at each crossing and junction.
Everyone is routed once with every section treated; the places carrying the
most of that flow are then each tried alone, and the screen reports the time
saved by the people whose best route improves.  A whole street and its own
sections overlap, so the ranking lists each place once: a candidate sharing
links with a better one is folded into it, and the best of a whole street's
busiest sections is given beside it.  Savings are route-cost minutes
(generalized cost: time plus the preset's stress and crossing costs), not
clock minutes.
This is a screen for where to look, not a design or cost estimate.
"""

from __future__ import annotations

import copy
import itertools
import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, cast

import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely.ops import unary_union

from walkbike_routes.analysis.barriers import barrier_sections
from walkbike_routes.analysis.interventions import generalized_cost_reduction_screen
from walkbike_routes.analysis.places import (
    JUNCTION,
    Places,
    crossing_ways,
    edge_names,
    junction_groups,
    way_of,
)
from walkbike_routes.models import Mode
from walkbike_routes.network.base import routable_links_to_graph
from walkbike_routes.network.broach import BroachMovements, facility_kind, traffic_band
from walkbike_routes.network.broach_walk import (
    BUSY_HIGHWAYS,
    WalkBroachMovements,
    signalised_crossing,
)
from walkbike_routes.routing import local
from walkbike_routes.routing.turns import SINK, STATE, Transition, sink, turn_graph


@dataclass(frozen=True)
class Treatment:
    """One change the screen tries: its name, its mode, and where it goes."""

    key: str
    label: str
    mode: Mode
    at_crossings: bool  # tried at crossings and junctions, not along street sections


BIKE_LANE = Treatment("bike_lane", "protected bike lane", Mode.BICYCLE, False)
BIKE_SIGNAL = Treatment("signal", "signal or crossing beacon", Mode.BICYCLE, True)
SIDEWALK = Treatment("sidewalk", "sidewalk", Mode.PEDESTRIAN, False)
CROSSWALK = Treatment("crosswalk", "marked crosswalk", Mode.PEDESTRIAN, True)
WALK_SIGNAL = Treatment("signal", "signal or crossing beacon", Mode.PEDESTRIAN, True)
TREATMENTS: dict[Mode, tuple[Treatment, ...]] = {
    Mode.BICYCLE: (BIKE_LANE, BIKE_SIGNAL),
    Mode.PEDESTRIAN: (SIDEWALK, CROSSWALK, WALK_SIGNAL),
}
# A junction treatment's cost for movement u-v-w, or None where it does not apply.
JunctionCost = Callable[[str, str, str], float | None]
STRESSFUL_LTS = 3
ORDER, ASCENDING = ["treated_flow", "length_m", "section_id"], [False, True, True]
SECTION_COLUMNS = ["section_id", "name", "label", "physical_edge_ids", "edge_count", "length_m"]
PARTS_PER_STREET = 3  # a whole street's busiest sections, scored to name its best part
BUSY_BAND = 2  # Broach's traffic bands: 2 is 10,000-20,000 vehicles a day
# Places saving less than this (0.0 to one decimal) are not listed.
MIN_MINUTES_SAVED = 0.05
# Reports list, for each kind of change, at most this many places, and only
# those saving at least this share of the best of that kind.
PRIORITY_ROWS = 5
PRIORITY_SHARE = 0.10
SHARE_TOLERANCE = 1e-9  # relative

SCREEN_COLUMNS = [
    "rank", "section_id", "label", "treatment", "length_m", "treated_flow",
    "benefiting_weight", "weighted_generalized_minutes_saved",
    "mean_generalized_minutes_saved_benefiting", "maximum_generalized_minutes_saved",
    "analyzed_weight", "best_part", "best_part_minutes_saved", "physical_edge_ids", "geometry",
]  # fmt: skip


def busy_without_protection(edges: gpd.GeoDataFrame) -> pd.Series:
    """Streets with 10,000+ vehicles a day and no protected lane or path.

    Broach's model charges riders for that traffic however the street rates
    by level of traffic stress, so a street at 25 mph that rates LTS 2 can
    still be costly to ride; a protected lane removes the charge.
    """
    column = edges.get("traffic_aadt", pd.Series(None, index=edges.index, dtype=object))
    highway = edges.get("highway", pd.Series(None, index=edges.index, dtype=object))
    lanes = edges.get("lanes", pd.Series(None, index=edges.index, dtype=object))
    facility = edges.get("bike_facility", pd.Series(None, index=edges.index, dtype=object))
    return pd.Series(
        [
            traffic_band(h, n, c) >= BUSY_BAND and facility_kind(f) in {"none", "boulevard", "lane"}
            for h, n, c, f in zip(highway, lanes, column, facility, strict=True)
        ],
        index=edges.index,
    )


def treatments(mode: Mode) -> tuple[Treatment, ...]:
    """The treatments screened for ``mode``."""
    if mode not in TREATMENTS:
        raise ValueError(f"no improvement treatment for {mode.value}")
    return TREATMENTS[mode]


def treat_edges(edges: gpd.GeoDataFrame, treatment: Treatment) -> gpd.GeoDataFrame:
    """The prepared streets with ``treatment`` applied everywhere it could help."""
    treated = edges.copy()
    if treatment is BIKE_LANE:
        lts = pd.to_numeric(
            treated.get("lts_best", pd.Series(math.nan, index=treated.index)), errors="coerce"
        )
        allowed = treated.get("bike_allowed", pd.Series(True, index=treated.index))
        allowed = allowed.fillna(True).astype(bool)
        busy = busy_without_protection(treated)
        mask = (lts.ge(STRESSFUL_LTS) | busy) & allowed
        treated.loc[mask, "lts_best"] = 1
        if "lts_furth" in treated:  # bike/furth reads Furth's levels
            furth = pd.to_numeric(treated.lts_furth, errors="coerce")
            treated.loc[(furth.ge(STRESSFUL_LTS) | busy) & allowed, "lts_furth"] = 1
        # The facility that makes a street low stress, for presets that read
        # facilities rather than LTS (bike/broach-2011).
        treated.loc[mask, "bike_facility"] = "track"
        return treated
    if treatment is BIKE_SIGNAL:
        return treated  # junctions only: see junction_cost
    if treatment.mode is not Mode.PEDESTRIAN:
        raise ValueError(f"unknown treatment {treatment.key!r}")
    status = treated.get("pedestrian_status", pd.Series("unknown", index=treated.index))
    status = status.fillna("unknown").astype(str)
    allowed = treated.get("ped_allowed", pd.Series(True, index=treated.index))
    allowed = allowed.fillna(True).astype(bool)
    crossing = crossing_ways(treated)
    if treatment is SIDEWALK:
        treated.loc[status.ne("supported") & allowed & ~crossing, "pedestrian_status"] = "supported"
        return treated
    signalled = pd.Series(
        [
            signalised_crossing({str(k): v for k, v in row.items()})
            for row in treated.drop(columns="geometry").to_dict("records")
        ],
        index=treated.index,
        dtype=bool,
    )
    marking = treated.get("crossing_marking", pd.Series("", index=treated.index))
    marking = marking.fillna("").astype(str)
    if treatment is CROSSWALK:
        mask = crossing & allowed & ~signalled & (marking.ne("marked") | status.ne("supported"))
    elif treatment is WALK_SIGNAL:
        road = treated.get("crossed_road", pd.Series("", index=treated.index))
        mask = crossing & allowed & ~signalled & road.fillna("").astype(str).isin(BUSY_HIGHWAYS)
        treated["crossing"] = treated.get("crossing", pd.Series("", index=treated.index))
        treated.loc[mask, "crossing"] = "traffic_signals"
    else:
        raise ValueError(f"unknown treatment {treatment.key!r}")
    treated["crossing_marking"] = marking
    treated.loc[mask, "crossing_marking"] = "marked"
    treated.loc[mask, "pedestrian_status"] = "supported"
    return treated


def add_signal_wait(
    links: gpd.GeoDataFrame,
    baseline: gpd.GeoDataFrame,
    treated: gpd.GeoDataFrame,
    wait_s: float,
    in_cost: bool = True,
) -> gpd.GeoDataFrame:
    """Charge a new crossing signal's wait on the crossing links it was put on.

    A signal added to a crossing way is found from the treated streets'
    ``crossing`` tag; its wait (split over the crossing's pieces by
    ``crossing_share``) goes on travel time and generalized cost.  A link
    with ``movement_type`` "across" already pays the wait when the links are
    rebuilt (the signal wait inventory reads the tag), so it is left alone.
    With ``in_cost`` False (a preset that keeps waits out of its cost, as
    ``walk/broach-2016`` does), the wait goes on travel time only.
    """
    before = dict(
        zip(
            baseline.physical_edge_id.astype(str),
            baseline.get("crossing", pd.Series("", index=baseline.index)).fillna("").astype(str),
            strict=True,
        )
    )
    share = dict(zip(treated.physical_edge_id.astype(str), pd.to_numeric(treated.get(
        "crossing_share", pd.Series(1.0, index=treated.index)), errors="coerce").fillna(1.0),
        strict=True))  # fmt: skip
    movement = treated.get("movement_type", pd.Series("", index=treated.index)).astype(str)
    added = {
        str(pid) for pid, tag, move in zip(
            treated.physical_edge_id, treated.crossing, movement, strict=True)
        if tag == "traffic_signals" and before.get(str(pid)) != "traffic_signals"
        and move != "across"
    } if "crossing" in treated else set()  # fmt: skip
    out = links.copy()
    hit = out.physical_edge_id.astype(str).isin(added)
    extra = out.physical_edge_id.astype(str).map(share).fillna(1.0) * wait_s
    for column in ("travel_time_s", "generalized_cost_s") if in_cost else ("travel_time_s",):
        out.loc[hit, column] = out.loc[hit, column].astype(float) + extra[hit]
    return out


class _Everywhere(set[str]):
    """A set holding every node: a treatment put on all junctions at once."""

    def __contains__(self, _item: object) -> bool:
        return True


def junction_cost(movements: Transition | None, treatment: Treatment) -> JunctionCost | None:
    """The preset's movement cost at a junction given ``treatment``, if it applies there.

    The returned function gives the treated cost of movement u-v-w at
    ``v``, or None where the treatment would not lower it.
    """
    if isinstance(movements, BroachMovements) and treatment is BIKE_SIGNAL:
        bike = movements
        signalled = copy.copy(bike)
        signalled.signalised = _Everywhere()
        # The model's signal term; its wait is part of it (see build_broach_links).
        term = bike.profile.ratio("signals_per_mi") * bike.mile_s

        def bike_signal(u: str, v: str, w: str) -> float | None:
            if v in bike.signalised or not bike.junctions.junction(v):
                return None
            new = signalled(u, v, w).generalized_cost_s + term
            return new if new < bike(u, v, w).generalized_cost_s - 1e-9 else None

        return bike_signal
    if isinstance(movements, WalkBroachMovements) and treatment in (CROSSWALK, WALK_SIGNAL):
        walk = movements
        changed = copy.copy(walk)
        if treatment is CROSSWALK:
            changed.marked = _Everywhere()
        else:
            changed.signalised = _Everywhere()
        wait = walk.profile.signal_wait_s if treatment is WALK_SIGNAL else 0.0

        def walk_crossing(u: str, v: str, w: str) -> float | None:
            if not walk.crossed(u, v, w):  # no unsignalised crossing of a busy road here
                return None
            new = changed(u, v, w).generalized_cost_s + wait
            return new if new < walk(u, v, w).generalized_cost_s - 1e-9 else None

        return walk_crossing
    return None


def screen_graph(
    baseline: gpd.GeoDataFrame, treated: gpd.GeoDataFrame, *, preferred_cost: str
) -> tuple[nx.DiGraph[str], frozenset[str]]:
    """The routing graph for a screen, and the ids of the links the treatment improves.

    Every graph edge gets ``treated_cost_s`` (its cost once treated) and
    ``baseline_available`` (false for links that exist only when treated).
    Links only the treatment opens are added in the directions they run,
    wherever no baseline link already joins the same two nodes that way.
    Like the rest of the routing, the graph keeps one link per ordered node
    pair, so of two parallel baseline links only the cheaper is screened.
    """
    cost = dict(zip(treated.routable_link_id.astype(str), treated.generalized_cost_s, strict=True))
    base = baseline.copy()
    ids = base.routable_link_id.astype(str)
    base["baseline_available"] = True
    base["treated_cost_s"] = [
        min(float(c), float(cost.get(i, c)))
        for i, c in zip(ids, base.generalized_cost_s, strict=True)
    ]
    extra = treated[~treated.routable_link_id.astype(str).isin(set(ids))].copy()
    extra["baseline_available"] = False
    extra["treated_cost_s"] = extra.generalized_cost_s.astype(float)
    graph = cast(
        "nx.DiGraph[str]",
        routable_links_to_graph(base, directed=True, preferred_cost=preferred_cost),
    )
    opened = routable_links_to_graph(extra, directed=True, preferred_cost=preferred_cost)
    for u, v, attrs in opened.edges(data=True):
        if not graph.has_edge(u, v):
            graph.add_edge(u, v, **attrs)
    improved = {
        str(attrs["routable_link_id"])
        for _, _, attrs in graph.edges(data=True)
        if not attrs["baseline_available"]
        or float(attrs["treated_cost_s"]) < float(attrs["generalized_cost_s"]) - 1e-9
    }
    return graph, frozenset(improved)


def movement_screen_graph(
    graph: nx.DiGraph[str], transition: Transition, junction: JunctionCost | None = None
) -> nx.DiGraph[str]:
    """A :func:`screen_graph` expanded into movements, so turn and crossing costs count.

    Each movement adds ``transition``'s cost to both the baseline and the
    treated cost.  With ``junction``, a movement whose cost the treatment
    lowers takes the treated movement cost instead, and is keyed to its
    junction (``intervention_key`` ``J:<node>``) so it can be tried alone;
    a movement onto a treated link stays with the link.  The virtual links
    joining states to their sinks are always available.
    """
    expanded = turn_graph(graph, transition, also=("treated_cost_s",))
    for a, b, attrs in expanded.edges(data=True):
        if attrs.get("virtual"):
            attrs["baseline_available"] = True
            continue
        if junction is None or a.startswith(SINK) or a.count(STATE) != 1 or b.count(STATE) != 1:
            continue  # a start node, or a state that is no plain arrival u-v
        u, v = a.split(STATE)
        w = b.split(STATE)[1]
        new = junction(u, v, w)
        if new is None:
            continue
        movement = transition(u, v, w).generalized_cost_s
        link, link_treated = (
            float(attrs["generalized_cost_s"]) - movement,
            float(attrs["treated_cost_s"]) - movement,
        )
        if link_treated < link - 1e-9:
            continue
        attrs["treated_cost_s"] = link + new
        attrs["intervention_key"] = JUNCTION + v
    return expanded


class NotExact(Exception):
    """A screen near the destination might differ from the whole network's."""

    def __init__(self, radius_m: float) -> None:
        super().__init__(f"needs a radius of {radius_m:.0f} m")
        self.radius_m = radius_m


def screen_cost_per_metre(graph: nx.DiGraph[str], xy: Mapping[str, tuple[float, float]]) -> float:
    """The least cost per metre of straight line of any link in a screen graph, today or treated.

    Turn and crossing costs only add to a link's (they may not be negative),
    so this bounds the movement graph too.
    """
    least = math.inf
    for u, v, attrs in graph.edges(data=True):
        cost = min(float(attrs["generalized_cost_s"]), float(attrs["treated_cost_s"]))
        a, b = xy.get(str(u)), xy.get(str(v))
        if not cost >= 0 or a is None or b is None:
            return math.nan
        metres = math.hypot(a[0] - b[0], a[1] - b[1])
        if metres > 0:
            least = min(least, cost / metres)
    return least


def _check_near(
    near: local.Near,
    search: nx.DiGraph[str],
    targets: list[str],
    origins: pd.DataFrame,
    reachable: set[str] | None,
) -> None:
    """Raise :class:`NotExact` unless every origin's cost today is below its bound."""
    present = [t for t in targets if t in search]
    costs: Mapping[str, float] = {}
    if present:
        costs = nx.multi_source_dijkstra_path_length(
            search.reverse(copy=False),
            present,
            weight=lambda _u, _v, a: (
                float(a["generalized_cost_s"]) if a.get("baseline_available", True) else None
            ),
        )
    ok, needed = local.exact_homes(near, costs, origins.node_id.astype(str), reachable)
    if not ok:
        raise NotExact(needed)


def treated_flows(
    graph: nx.DiGraph[str], destination_nodes: Iterable[str], origins: pd.DataFrame
) -> dict[str, float]:
    """Weight on each physical edge, and junction key, when everyone takes their
    best fully treated route."""
    destinations = [str(n) for n in destination_nodes if str(n) in graph]
    if not destinations:
        return {}
    search = graph.reverse(copy=False)
    _, paths = cast(
        "tuple[dict[str, float], dict[str, list[str]]]",
        nx.multi_source_dijkstra(search, destinations, weight="treated_cost_s"),
    )
    totals: defaultdict[str, float] = defaultdict(float)
    weights = origins.groupby(origins.node_id.astype(str)).weight.sum()
    for node, weight in weights.items():
        path = paths.get(str(node))
        if path is None:
            continue
        for a, b in itertools.pairwise(path):
            attrs = search[a][b]
            if edge := attrs.get("physical_edge_id"):  # not a movement graph's virtual link
                totals[str(edge)] += float(weight)
            if key := attrs.get("intervention_key"):  # a junction treatment's movement
                totals[str(key)] += float(weight)
    return dict(totals)


def whole_streets(sections: pd.DataFrame) -> pd.DataFrame:
    """One candidate per named street with several of ``sections``: all at once.

    A single block of a protected lane or sidewalk often changes nobody's
    route; treating the street can.  Pass only the sections people would use
    once treated, so the candidate is the useful stretch, not the whole street.
    """
    rows = []
    named = sections[~sections.name.astype(str).str.match(r"^(unnamed\b|$)")]
    for name, group in named.groupby("name", sort=True):
        if len(group) < 2:
            continue
        ids = sorted({p for value in group.physical_edge_ids for p in str(value).split(";")})
        rows.append(
            {
                "section_id": "W" + "+".join(group.section_id.astype(str)),
                "name": name,
                "label": f"{name} ({len(group)} sections)",
                "physical_edge_ids": ";".join(ids),
                "edge_count": len(ids),
                "length_m": float(group.length_m.sum()),
            }
        )
    return pd.DataFrame(rows, columns=list(sections.columns))


def fold_overlaps(table: pd.DataFrame) -> pd.DataFrame:
    """Keep each candidate that shares no link with a better one; fold the rest in.

    ``table`` is in order, best first.  A folded candidate (a section of a
    whole street listed above it) is dropped, and the best one folded into
    each kept row that saves anything is named in ``best_part``, with its
    ``best_part_minutes_saved``.
    """
    kept: list[Any] = []
    links: list[set[str]] = []
    part: dict[Any, tuple[str, float]] = {}
    for index, ids, label, saved in zip(
        table.index, table.physical_edge_ids, table.label,
        table.weighted_generalized_minutes_saved, strict=True,
    ):  # fmt: skip
        own = set(str(ids).split(";"))
        owner = next((k for k, taken in zip(kept, links, strict=True) if own & taken), None)
        if owner is None:
            kept.append(index)
            links.append(own)
        elif owner not in part and float(saved) > 0:  # the first folded in is the best
            part[owner] = (str(label), float(saved))
    folded = table.loc[kept].copy()
    folded["best_part"] = [part.get(k, ("", math.nan))[0] for k in kept]
    folded["best_part_minutes_saved"] = [part.get(k, ("", math.nan))[1] for k in kept]
    return folded


def _street_places(
    graph: nx.DiGraph[str],
    improved: frozenset[str],
    names: Mapping[str, str],
    flow: Mapping[str, float],
    candidates: int,
) -> pd.DataFrame:
    """Street candidates: whole streets and sections not on one, plus each
    chosen street's busiest sections, scored to name its best part."""

    def is_barrier(attrs: Mapping[str, Any]) -> bool:
        return str(attrs.get("routable_link_id")) in improved

    _, sections = barrier_sections(graph, is_barrier, name_of=lambda pid, _a: names.get(pid, ""))
    if sections.empty:
        return sections
    sections["treated_flow"] = [
        max(flow.get(p, 0.0) for p in ids.split(";")) for ids in sections.physical_edge_ids
    ]
    used = sections[sections.treated_flow.gt(0)]
    streets = whole_streets(used)
    streets["treated_flow"] = [
        max(flow.get(p, 0.0) for p in ids.split(";")) for ids in streets.physical_edge_ids
    ]
    on_street = used.name.isin(set(streets.name))
    places = pd.concat([used[~on_street], streets], ignore_index=True)
    places = places.sort_values(ORDER, ascending=ASCENDING).head(candidates)
    parts = used[on_street & used.name.isin(set(places.name))]
    parts = (
        parts.sort_values(ORDER, ascending=ASCENDING)
        .groupby("name", sort=False)
        .head(PARTS_PER_STREET)
    )
    return pd.concat([places, parts], ignore_index=True)


def _crossing_places(
    graph: nx.DiGraph[str],
    improved: frozenset[str],
    places: Places,
    flow: Mapping[str, float],
    candidates: int,
) -> pd.DataFrame:
    """Crossing candidates: each treated crossing way, and each treated junction."""
    pieces: defaultdict[str, set[str]] = defaultdict(set)
    for _, _, attrs in graph.edges(data=True):
        if str(attrs.get("routable_link_id")) in improved:
            pid = str(attrs.get("physical_edge_id"))
            pieces[places.crossing_of.get(pid, way_of(pid))].add(pid)
    rows = [
        {
            "section_id": f"X:{way}",
            "name": "",
            "label": places.crossing(ids),
            "physical_edge_ids": ";".join(sorted(ids)),
            "edge_count": len(ids),
            "length_m": places.length(ids),
            "treated_flow": max(flow.get(p, 0.0) for p in ids),
        }
        for way, ids in pieces.items()
    ]
    rows += [
        {
            "section_id": keys[0], "name": "", "label": label,
            "physical_edge_ids": ";".join(keys), "edge_count": len(keys), "length_m": 0.0,
            "treated_flow": max(flow[k] for k in keys),
        }
        for label, keys in junction_groups(places, flow)
    ]  # fmt: skip
    table = pd.DataFrame(rows, columns=[*SECTION_COLUMNS, "treated_flow"])
    table = table[table.treated_flow.gt(0)]
    return table.sort_values(ORDER, ascending=ASCENDING).head(candidates)


def improvement_screen(
    graph: nx.DiGraph[str],
    destination_nodes: Iterable[str],
    origins: pd.DataFrame,
    *,
    improved: frozenset[str],
    edges: gpd.GeoDataFrame,
    mode: Mode,
    candidates: int,
    time_limit_min: float | None,
    movements: Transition | None = None,
    treatment: Treatment | None = None,
    junction: JunctionCost | None = None,
    near: local.Near | None = None,
    reachable: set[str] | None = None,
) -> gpd.GeoDataFrame:
    """Rank the places where ``treatment`` saves the most route-cost minutes (generalized cost).

    Places that save less than :data:`MIN_MINUTES_SAVED` weighted minutes
    (0.0 to one decimal) are left out, so fewer than ``candidates`` may be
    listed; so is a whole street's best section when it saves that little.

    ``treatment`` defaults to the mode's street treatment.  For a street
    treatment, ``candidates`` places are tried: whole streets and sections not
    on one, by their flow once everything is treated, and each whole street's
    busiest sections are tried as well and folded into it (see
    :func:`fold_overlaps`).  For a crossing treatment, the candidates are
    crossing ways and, with ``junction`` (from :func:`junction_cost`), the
    junctions where it lowers the movement cost.

    ``graph`` comes from :func:`screen_graph`; ``origins`` needs ``node_id``,
    ``weight`` and ``snap_travel_time_s``.  Only people who can reach the
    destination today are counted; with ``time_limit_min`` only those whose
    best route today fits the limit.  An optional ``within_limit`` column marks
    them, so candidates are found from the same people's routes: a place only
    people beyond the limit would use saves nothing.  With ``movements`` (a
    preset's turn and crossing costs), sections are found on ``graph`` but
    routes are found on its movement graph, so those costs count in the
    minutes saved.

    With ``near`` (from :func:`walkbike_routes.routing.local.near`, its
    ``per_m`` from :func:`screen_cost_per_metre`), routes are found only on
    the streets in its circle; candidates are still drawn from ``graph``, and
    each holds a street some route uses, so it is in the circle.  A treatment only lowers costs, so when every
    origin's cost today is below its bound, every candidate's is too, and the
    result is the whole network's; otherwise :class:`NotExact` is raised with
    the radius asked for.  ``reachable`` (street nodes that reach the
    destination on the whole network, by :func:`~walkbike_routes.routing.local.reaches`)
    lets an origin that reaches nothing anywhere pass.
    """
    treatment = treatment or treatments(mode)[0]
    destinations = [str(n) for n in destination_nodes]  # read once; it may be an iterator
    # Routes are found near the destination; places are drawn from the whole
    # network, so a street's sections and their names are the same either way.
    routed = graph if near is None else cast("nx.DiGraph[str]", near.graph)
    search, targets = routed, [n for n in destinations if n in routed]
    if movements is not None:
        search = movement_screen_graph(routed, movements, junction)
        targets = [sink(n) for n in destinations if n in routed]
    if near is not None:
        _check_near(near, search, targets, origins, reachable)
    empty = gpd.GeoDataFrame(columns=SCREEN_COLUMNS, geometry="geometry", crs=edges.crs)
    if candidates <= 0 or not (improved or (junction is not None and movements is not None)):
        return empty
    cohort = origins
    if time_limit_min and "within_limit" in origins:
        cohort = origins[origins.within_limit.astype(bool)]
    flow = treated_flows(search, targets, cohort)
    places = Places(edges)
    if treatment.at_crossings:
        top = _crossing_places(graph, improved, places, flow, candidates)
    else:
        top = _street_places(graph, improved, edge_names(edges), flow, candidates)
    if top.empty:
        return empty
    result = generalized_cost_reduction_screen(
        search,
        targets,
        origins,
        {
            str(section): str(ids).split(";")
            for section, ids in zip(top.section_id, top.physical_edge_ids, strict=True)
        },
        lambda attrs: float(attrs["treated_cost_s"]),
        cost_field="generalized_cost_s",
        cost_units_per_minute=60.0,
        physical_time_field="travel_time_s",
        origin_access_time_field="snap_travel_time_s",
        physical_time_limits_minutes=(time_limit_min,) if time_limit_min else (),
        baseline_available_field="baseline_available",
    )
    table = top.merge(result, left_on="section_id", right_on="intervention_id")
    # On a tie the bigger place goes first, so a whole street saving no more
    # than its best section is listed as the street, with that section as its part.
    table = table.sort_values(
        ["weighted_generalized_minutes_saved", "edge_count", "section_id"],
        ascending=[False, False, True],
    )
    table = fold_overlaps(table)
    # A place that saves nothing is not listed: the list may be shorter than
    # ``candidates`` rather than padded with places that do not help.
    saved = table.weighted_generalized_minutes_saved.astype(float)
    table = table[saved >= MIN_MINUTES_SAVED].reset_index(drop=True)
    if table.empty:
        return empty
    # Nor is a best section that saves nothing on its own.
    weak = table.best_part_minutes_saved.astype(float) < MIN_MINUTES_SAVED
    table.loc[weak, "best_part"] = ""
    table.loc[weak, "best_part_minutes_saved"] = math.nan
    table["rank"] = range(1, len(table) + 1)
    table["treatment"] = treatment.label
    table["geometry"] = gpd.GeoSeries(
        [
            unary_union(
                [
                    places.point(p) if p.startswith(JUNCTION) else places.lines[p]
                    for p in str(ids).split(";")
                ]
            )
            for ids in table.physical_edge_ids
        ],
        index=table.index,
        crs=edges.crs,
    )
    return gpd.GeoDataFrame(table[SCREEN_COLUMNS], geometry="geometry", crs=edges.crs)


def priorities(
    screen: pd.DataFrame, rows: int = PRIORITY_ROWS, share: float = PRIORITY_SHARE
) -> pd.DataFrame:
    """The places worth listing: for each kind of change, in the order the run wrote
    them, those saving at least ``share`` of the best of that kind, at most ``rows``.

    Kinds are cut separately, not ranked against each other: the screen knows what
    each change saves, not what it costs, and a signal costs far less than a long
    sidewalk.
    """
    if screen.empty:
        return screen
    kept = []
    for _, kind in screen.groupby(screen.treatment.astype(str), sort=False):
        ranked = kind.sort_values("rank")
        saved = ranked.weighted_generalized_minutes_saved.astype(float)
        # A place saving exactly the share is kept despite float roundoff
        # (0.1 * 3.0 is 0.30000000000000004).
        floor = share * saved.max() * (1 - SHARE_TOLERANCE)
        kept.append(ranked[saved >= floor].head(rows))
    return pd.concat(kept)
