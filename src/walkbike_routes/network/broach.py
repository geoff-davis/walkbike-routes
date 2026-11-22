"""Cycling route choice from Broach, Gliebe and Dill's Portland GPS model.

Source: Broach, J., Gliebe, J. and Dill, J. (2011), "Bicycle route choice
model developed using revealed preference GPS data", paper presented at the
90th Annual Meeting of the Transportation Research Board, Table 2: a
path-size logit estimated on 1,449 trips by 162 Portland cyclists.  The
published version (Transportation Research Part A 46(10), 2012) may differ.

Route utility is ``b_d ln(distance) + sum_k b_k x_k + b_ps ln(path size)``,
where each ``x_k`` is a share of the route's length (on a bike path, up a 2-4%
slope ...) or a count per mile (turns, signals ...).  Because distance enters
as a log, each term is worth a fixed share of the route's distance.  Taking
the first-order form, ``distance * (1 + sum_k (b_k / b_d) x_k)``, the terms
add up link by link:

- a share-of-length term adds ``b_k / b_d`` times the link's length;
- a per-mile count adds ``b_k / b_d`` miles for each event.

This is exact for small shares and counts and understates a route that is
mostly steep or busy (the model's own form is exponential in them).  Costs
are this equivalent distance at the profile's flat speed, in seconds.

Mapping to this tool's network, all assumptions:

- a street's traffic band comes from its daily count (``traffic_aadt``,
  usually federal HPMS counts) where it has one; otherwise from road class:
  tertiary 5-10k a day, secondary 10-20k, primary 20-30k, trunk and
  motorway 30k+, a slip lane off an ordinary road under 5k and a trunk or
  motorway ramp 10-20k, and a road with 4 or more lanes is at least 20-30k;
- a bike lane or a separated track removes the traffic term, as the model's
  bike lanes do (it has no separate term for tracks).  Two settings, both
  assumptions, change that: ``lane_traffic_relief`` is the share of the
  term a painted lane removes (1 in the paper; ``bike/broach-2011-child``
  uses 0.25), and ``protected_factor`` scales a track's cost against the
  same street with a lane that removes all of it (0.72 in every Broach
  preset, after Fosgerau et al. 2023).  The model gives a
  lane no value of its own, so on a street under 10k a day a lane is worth
  nothing.  A profile's ``lane_term`` (an assumption, not in the paper)
  also gives every lane and track another of the model's facility terms,
  such as the bike boulevard's, and its ``stress_scale`` (also an
  assumption, for ``bike/broach-2011-child``) multiplies the busy-traffic
  terms and the turn and crossing terms for roads without a signal;
- a bike path is a cycleway or a path designated for bikes; footways that
  allow bikes, often sidewalks, are not (the model excludes sidewalks and
  minor park paths);
- a bike boulevard is a street tagged ``bicycle_road`` or ``cyclestreet``;
- turns and crossings depend on the movement through a node, so they are
  costed by :class:`BroachMovements` on a graph of movements
  (:mod:`walkbike_routes.routing.turns`), not on links.  Only a node where
  three or more ways meet has turns: a change of heading of 45 degrees or
  more, measured over the 10 m of each link next to the node.  The paper
  used street names and geometry; this uses geometry alone;
- a left turn without a signal off a road of 10-20k or 20k+ a day (the
  paper's "parallel traffic volume", read here as the road being left)
  adds the left-turn term;
- going straight on or turning left at a node without a signal where a road
  busier than the one ridden meets it pays that road's crossing term; a
  right turn onto a busier road of 10k+ pays the right-turn term.
  Links with the ridden street's name are the same road, so a count or road
  class changing along it is no crossing;
- a signal drawn as several nodes (separate carriageways, short links
  between them) is one junction for turns: the turn is judged from the
  link entering it to the link leaving it (:meth:`BroachMovements.through`);
- a slow signal (a correction's ``extra_wait_s``) adds that wait to every
  passage through it, except a right turn where right on red is allowed
  (OSM ``red_turn:right``, or the correction; allowed unless said
  otherwise).  Travel time keeps the ordinary signal wait;
- the model's bridge terms, specific to Portland's river crossings, are
  left out, and the signal term, costed on links, is also charged on right
  turns, which the model's term excludes.

Signal waits stay in travel time, so time limits are as for the other
presets, and the model's signal term replaces them in the cost: the model's
term already includes the delay.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import geopandas as gpd
import networkx as nx
import pandas as pd

from walkbike_routes.network.base import costs_are_valid
from walkbike_routes.network.bicycle import BicycleProfile, build_bicycle_links
from walkbike_routes.network.signals import SignalWaitInventory, build_signal_wait_inventory
from walkbike_routes.numeric import require_finite_settings
from walkbike_routes.routing.costs import CostComponents
from walkbike_routes.routing.turns import Junctions
from walkbike_routes.units import mph_to_mps

METRES_PER_MILE = 1609.344
TRIPS = ("noncommute", "commute")

# Coefficient names, as in Table 2.  "_commute" terms are added on commute trips.
COEFFICIENTS = frozenset(
    {
        "ln_distance", "ln_distance_commute", "turns_per_mi",
        "upslope_2_4", "upslope_4_6", "upslope_6_plus",
        "signals_per_mi", "stop_signs_per_mi",
        "left_turn_unsignalised_10_20k_per_mi", "left_turn_unsignalised_20k_plus_per_mi",
        "unsignalised_cross_10k_plus_right_turn_per_mi",
        "unsignalised_cross_5_10k_per_mi", "unsignalised_cross_10_20k_per_mi",
        "unsignalised_cross_20k_plus_per_mi",
        "bike_boulevard", "bike_path",
        "traffic_10_20k_no_lane", "traffic_10_20k_no_lane_commute",
        "traffic_20_30k_no_lane", "traffic_20_30k_no_lane_commute",
        "traffic_30k_plus_no_lane", "traffic_30k_plus_no_lane_commute",
        "bridge_bike_lane", "bridge_separated",
        "ln_path_size",
    }
)  # fmt: skip

# Traffic bands, by daily vehicles: -1 no motor traffic, 0 under 5k, 1 5-10k,
# 2 10-20k, 3 20-30k, 4 30k+.
AADT_BAND_LIMITS = (5_000, 10_000, 20_000, 30_000)
BAND_BY_HIGHWAY = {
    "tertiary": 1, "secondary": 2, "primary": 3, "trunk": 4, "motorway": 4,
    # A link road (a slip lane or turning roadway) carries only the traffic
    # turning through it, not its road's: under 5k off ordinary roads, and
    # 10-20k on a trunk or motorway ramp.  Assumptions; a count overrides them.
    "tertiary_link": 0, "secondary_link": 0, "primary_link": 0,
    "trunk_link": 2, "motorway_link": 2,
}  # fmt: skip
NO_MOTOR_HIGHWAYS = frozenset({"cycleway", "path", "footway", "pedestrian", "steps", "track"})
BIKE_PATH_HIGHWAYS = frozenset({"cycleway", "path"})
LANE_FACILITIES = frozenset({"lane", "track"})
# The terms a profile's stress_scale multiplies: busy traffic, and turns and
# crossings without a signal.
STRESS_TERMS = frozenset(
    {
        "traffic_10_20k_no_lane", "traffic_20_30k_no_lane", "traffic_30k_plus_no_lane",
        "left_turn_unsignalised_10_20k_per_mi", "left_turn_unsignalised_20k_plus_per_mi",
        "unsignalised_cross_10k_plus_right_turn_per_mi", "unsignalised_cross_5_10k_per_mi",
        "unsignalised_cross_10_20k_per_mi", "unsignalised_cross_20k_plus_per_mi",
    }
)  # fmt: skip
# Terms a profile may borrow for bike lanes and tracks (see ``BroachProfile.lane_term``).
LANE_TERMS = frozenset({"bike_boulevard", "bike_path"})
MIN_LINK_FACTOR = 0.05
TURN_DEG = 45.0  # a change of heading at least this large at an intersection is a turn
LEFT_TURN_TERMS = ("left_turn_unsignalised_10_20k_per_mi", "left_turn_unsignalised_20k_plus_per_mi")
CROSSING_TERMS = (  # by the crossed road's band, 1 (5-10k) to 4 (30k+)
    "unsignalised_cross_5_10k_per_mi",
    "unsignalised_cross_10_20k_per_mi",
    "unsignalised_cross_20k_plus_per_mi",
    "unsignalised_cross_20k_plus_per_mi",
)


@dataclass(frozen=True)
class BroachProfile:
    """Speed, waits and the published coefficients for one trip purpose."""

    profile_id: str
    coefficients: Mapping[str, float] = field(default_factory=dict)
    trip: str = "noncommute"
    speed_mph: float = 10.0
    signal_wait_s: float = 20.0
    signal_wait_cross_arterial_s: float | None = None
    signal_wait_along_arterial_s: float | None = None
    lane_term: str | None = None
    stress_scale: float = 1.0  # not in the paper: multiplies STRESS_TERMS
    # Not in the paper: a protected lane (track) counts as this times the same
    # street with a painted lane that removes all the busy-traffic term, apart
    # from hills (Fosgerau et al. 2023: 0.72).
    protected_factor: float = 1.0
    # Not in the paper: the share of the busy-traffic term a painted lane
    # removes; the paper's lanes remove all of it.
    lane_traffic_relief: float = 1.0

    def __post_init__(self) -> None:
        require_finite_settings(self)
        missing = sorted(COEFFICIENTS - set(self.coefficients))
        extra = sorted(set(self.coefficients) - COEFFICIENTS)
        if missing or extra:
            raise ValueError(f"coefficients: missing {missing}, unknown {extra}")
        if self.trip not in TRIPS:
            raise ValueError(f"trip must be one of {TRIPS}")
        if self.distance_coefficient >= 0:
            raise ValueError("the distance coefficient must be negative")
        if self.speed_mph <= 0:
            raise ValueError("bicycle speed must be positive")
        if self.stress_scale <= 0:
            raise ValueError("stress_scale must be positive")
        if not 0 < self.protected_factor <= 1:
            raise ValueError("protected_factor must be above 0 and at most 1")
        if not 0 <= self.lane_traffic_relief <= 1:
            raise ValueError("lane_traffic_relief must be between 0 and 1")
        if self.lane_term is not None and self.lane_term not in LANE_TERMS:
            raise ValueError(f"lane_term must be one of {sorted(LANE_TERMS)}")

    @property
    def distance_coefficient(self) -> float:
        b = self.coefficients["ln_distance"]
        return b + self.coefficients["ln_distance_commute"] if self.trip == "commute" else b

    def ratio(self, name: str) -> float:
        """Term ``name`` as a share of distance: positive is a cost."""
        b = self.coefficients[name]
        if self.trip == "commute" and f"{name}_commute" in self.coefficients:
            b += self.coefficients[f"{name}_commute"]
        scale = self.stress_scale if name in STRESS_TERMS else 1.0
        return scale * b / self.distance_coefficient

    def timing(self) -> BicycleProfile:
        """The plain profile that gives travel time and signal waits."""
        return BicycleProfile(
            self.profile_id,
            speed_mph=self.speed_mph,
            lts_cost_factors=(1.0, 1.0, 1.0, 1.0),
            signal_wait_s=self.signal_wait_s,
            signal_wait_cross_arterial_s=self.signal_wait_cross_arterial_s,
            signal_wait_along_arterial_s=self.signal_wait_along_arterial_s,
        )


def _missing(value: Any) -> bool:
    return value is None or (pd.api.types.is_scalar(value) and bool(pd.isna(value)))


def _text(value: Any) -> str:
    return "" if _missing(value) else str(value).strip().lower()


def traffic_band(highway: Any, lanes: Any, aadt: Any = None) -> int:
    """The daily-traffic band of a way: from its count when it has one, else
    from its road class (see ``BAND_BY_HIGHWAY``)."""
    road = _text(highway)
    if road in NO_MOTOR_HIGHWAYS:
        return -1
    try:
        count = math.nan if aadt is None or _missing(aadt) else float(aadt)
    except (TypeError, ValueError):
        count = math.nan
    if math.isfinite(count) and count >= 0:
        return sum(count >= limit for limit in AADT_BAND_LIMITS)
    band = BAND_BY_HIGHWAY.get(road, 0)
    try:
        many_lanes = not _missing(lanes) and float(lanes) >= 4
    except (TypeError, ValueError):
        many_lanes = False
    return max(band, 3) if many_lanes and band >= 1 else band


def _upslope_ratio(profile: BroachProfile, grade: float) -> float:
    if not math.isfinite(grade) or grade < 0.02:
        return 0.0
    if grade < 0.04:
        return profile.ratio("upslope_2_4")
    if grade < 0.06:
        return profile.ratio("upslope_4_6")
    return profile.ratio("upslope_6_plus")


def facility_kind(value: Any) -> str:
    """``lane``, ``track``, ``path``, ``boulevard`` or ``none``, from any facility label.

    Accepts this tool's labels and the California classes local layers use
    (Class I path, II lane, IV separated bikeway).
    """
    text = _text(value) or "none"
    if text in {"class ii", "bike lane", "lane"} or text.startswith("class ii ("):
        return "lane"
    if text in {"class iv", "protected", "track"} or text.startswith("class iv ("):
        return "track"
    if text in {"class i", "separated path", "path"} or text.startswith("class i ("):
        return "path"
    if text in {"bike boulevard", "boulevard"}:
        return "boulevard"
    return "none"


def link_ratio(profile: BroachProfile, edge: Mapping[str, Any], grade: float) -> float:
    """The share of its own length a link adds (negative for paths and boulevards)."""
    facility = facility_kind(edge.get("bike_facility"))
    highway = _text(edge.get("highway"))
    band = traffic_band(highway, edge.get("lanes"), edge.get("traffic_aadt"))
    ratio = 0.0
    if facility in {"none", "boulevard", "lane", "track"} and band >= 2:
        traffic = profile.ratio(
            ("traffic_10_20k_no_lane", "traffic_20_30k_no_lane", "traffic_30k_plus_no_lane")[
                band - 2
            ]
        )
        # A painted lane removes lane_traffic_relief of the term; a track
        # removes all of it and is then scaled by protected_factor below.
        relief = {"lane": profile.lane_traffic_relief, "track": 1.0}.get(facility, 0.0)
        ratio += traffic * (1.0 - relief)
    if facility == "path" and highway in BIKE_PATH_HIGHWAYS:
        ratio += profile.ratio("bike_path")
    if facility == "boulevard":
        ratio += profile.ratio("bike_boulevard")
    if facility in LANE_FACILITIES and profile.lane_term is not None:
        ratio += profile.ratio(profile.lane_term)
    if facility == "track":
        ratio = (1.0 + ratio) * profile.protected_factor - 1.0
    return _upslope_ratio(profile, grade) + ratio


# A link this short between two nodes of one road is the gap between a divided
# road's carriageways (analysis.screens.JUNCTION_JOIN_M).
MEDIAN_LINK_M = 60.0


def _road(name: Any, edge_id: Any) -> str:
    """Which road an edge is part of: its name, or the edge itself if unnamed."""
    return _text(name) or f"edge {edge_id}"


def _bands_by_node(edges: gpd.GeoDataFrame) -> dict[str, list[tuple[int, str]]]:
    """For each node, the traffic band and road of every edge meeting it."""
    none = pd.Series(None, index=edges.index, dtype=object)
    highways = edges["highway"] if "highway" in edges else none
    lanes = edges["lanes"] if "lanes" in edges else none
    counts = edges["traffic_aadt"] if "traffic_aadt" in edges else none
    names = edges["name"] if "name" in edges else none
    found: dict[str, list[tuple[int, str]]] = {}
    for u, v, edge_id, highway, lane, count, name in zip(
        edges.u.astype(str), edges.v.astype(str), edges.physical_edge_id, highways, lanes,
        counts, names, strict=True,
    ):  # fmt: skip
        entry = (traffic_band(highway, lane, count), _road(name, edge_id))
        found.setdefault(u, []).append(entry)
        found.setdefault(v, []).append(entry)
    return found


def build_broach_links(
    physical_edges: gpd.GeoDataFrame,
    nodes: gpd.GeoDataFrame,
    profile: BroachProfile,
    *,
    signal_wait_inventory: SignalWaitInventory | None = None,
) -> gpd.GeoDataFrame:
    """Directed bicycle links costed by the Broach, Gliebe and Dill (2011) model.

    Without ``signal_wait_inventory``, it is built from ``nodes``.
    """
    if signal_wait_inventory is None:
        signal_wait_inventory = build_signal_wait_inventory(nodes, physical_edges)
    links = build_bicycle_links(
        physical_edges, profile.timing(), signal_wait_inventory=signal_wait_inventory
    )
    if links.empty:
        return links
    speed = mph_to_mps(profile.speed_mph)
    mile_s = METRES_PER_MILE / speed
    edges: dict[str, dict[str, Any]] = {
        str(row["physical_edge_id"]): {str(k): v for k, v in row.items()}
        for row in physical_edges.drop(columns="geometry").to_dict("records")
    }
    stop = pd.Series(False, index=nodes.index)
    for column in ("control", "highway"):  # this tool's control, or the OSM tag
        if column in nodes:
            stop |= nodes[column].astype(str).eq("stop")
    stops = set(nodes.node_id.astype(str)[stop])
    stress, facility, control = [], [], []
    for raw_link in links.itertuples(index=False):
        link: Any = raw_link
        edge = edges[str(link.physical_edge_id)]
        forward = str(link.direction) == "forward"
        to_node = str(link.v) if forward else str(link.u)
        value = edge.get("grade")
        raw_grade = math.nan if value is None or _missing(value) else float(value)
        grade = raw_grade if forward else -raw_grade
        ratio = link_ratio(profile, edge, grade)
        ratio = max(ratio, MIN_LINK_FACTOR - 1)
        movement = float(link.movement_time_s)
        stress.append(movement * max(ratio, 0.0))
        facility.append(movement * min(ratio, 0.0))
        if isinstance(link.signal_wait_event_id, str) and link.signal_wait_event_id:
            term = profile.ratio("signals_per_mi") * mile_s - float(link.signal_wait_s)
        elif to_node in stops:
            term = profile.ratio("stop_signs_per_mi") * mile_s
        else:
            term = 0.0
        control.append(term)
    out = links.copy()
    out["stress_cost_s"] = stress
    out["facility_cost_s"] = facility
    out["crossing_cost_s"] = 0.0  # crossings depend on the movement: see BroachMovements
    out["control_cost_s"] = control
    parts = ["travel_time_s", "stress_cost_s", "facility_cost_s", "crossing_cost_s",
             "turn_cost_s", "control_cost_s", "transition_cost_s"]  # fmt: skip
    out["generalized_cost_s"] = out[parts].sum(axis=1).to_numpy(float)
    out["link_kind"] = "bicycle_broach_2011"
    if not costs_are_valid(out):
        raise ValueError("a Broach link cost came out negative, or zero on a link with length")
    return out


def _slow_signals(
    nodes: gpd.GeoDataFrame, signals: SignalWaitInventory
) -> tuple[dict[str, float], frozenset[str]]:
    """Each signal node's extra wait, and the signal nodes where right on red is banned.

    Both apply to the whole signal (its cluster of nodes): the longest
    ``extra_wait_s`` given on any of its nodes, and a ban on any of them.
    """
    extra: dict[str, float] = {}
    banned: set[str] = set()
    given = pd.to_numeric(
        nodes.get("extra_wait_s", pd.Series(math.nan, index=nodes.index)), errors="coerce"
    ).to_numpy(dtype=float, na_value=math.nan)
    allowed = nodes.get("right_on_red", pd.Series("", index=nodes.index))
    for node, rule, wait in zip(nodes.node_id.astype(str), allowed, given, strict=True):
        cluster = signals.cluster_by_node.get(node)
        if cluster is None:
            continue
        if _text(rule) == "no":
            banned.add(cluster)
        if math.isfinite(wait) and wait > 0:
            extra[cluster] = max(extra.get(cluster, 0.0), float(wait))
    by_node = signals.cluster_by_node.items()
    return (
        {node: extra[cluster] for node, cluster in by_node if cluster in extra},
        frozenset(node for node, cluster in by_node if cluster in banned),
    )


class BroachMovements:
    """The model's turn and crossing terms for each movement ``u-v-w`` at a node.

    Call it with three node ids; pass it to
    :func:`~walkbike_routes.routing.turns.turn_graph`.  See the module notes
    for how movements are classified.
    """

    def __init__(
        self,
        graph: nx.DiGraph[str],
        physical_edges: gpd.GeoDataFrame,
        nodes: gpd.GeoDataFrame,
        profile: BroachProfile,
        *,
        signal_wait_inventory: SignalWaitInventory | None = None,
    ) -> None:
        if signal_wait_inventory is None:
            signal_wait_inventory = build_signal_wait_inventory(nodes, physical_edges)
        self.graph = graph
        self.profile = profile
        self.mile_s = METRES_PER_MILE / mph_to_mps(profile.speed_mph)
        self.junctions = Junctions(graph, physical_edges, nodes)
        self.bands_at = _bands_by_node(physical_edges)
        self.signalised = set(signal_wait_inventory.cluster_by_node)
        # A signal drawn as several nodes is one junction for turns (routing.turns).
        self.clusters = dict(signal_wait_inventory.cluster_by_node)
        self.extra_waits, self.no_right_on_red = _slow_signals(nodes, signal_wait_inventory)
        self.signal_legs = set(signal_wait_inventory.crossing_leg_edges)
        self.edges: dict[str, tuple[int, str, bool]] = {}
        self.median_links = set(
            physical_edges.physical_edge_id.astype(str)[
                (physical_edges.geometry.length <= MEDIAN_LINK_M).to_numpy()
            ]
        )
        for row in physical_edges.drop(columns="geometry").to_dict("records"):
            edge_id = str(row["physical_edge_id"])
            self.edges[edge_id] = (
                traffic_band(row.get("highway"), row.get("lanes"), row.get("traffic_aadt")),
                _road(row.get("name"), edge_id),
                edge_id in self.signal_legs or _text(row.get("crossing")) == "traffic_signals",
            )

    def through(self, a: str, b: str, x: str, w: str) -> CostComponents:
        """The movement through a signal, from link ``a-b`` entering it to ``x-w`` leaving.

        The turn is the change of heading between those two links, so the
        short links inside a junction drawn as several nodes add no turns.
        A signal has no unsignalised terms.  A slow signal's extra wait is
        paid by every passage except a right turn where right on red is
        allowed.
        """
        arriving = self.junctions.heading_away(a, b, b) + 180.0
        leaving = self.junctions.heading_away(x, w, x)
        change = (leaving - arriving + 180.0) % 360.0 - 180.0
        turned = abs(change) >= TURN_DEG  # an unknown heading counts as through
        turn = self.profile.ratio("turns_per_mi") * self.mile_s if turned else 0.0
        on_red = turned and change < 0 and b not in self.no_right_on_red
        held = 0.0 if on_red else self.extra_waits.get(b, 0.0)
        return CostComponents(turn_cost_s=turn, control_cost_s=held)

    def _across_the_gap(self, node: str, link: str) -> set[str]:
        """The roads at ``node`` when ``link`` leads to it over a divided road's gap."""
        if link not in self.median_links:
            return set()
        return {r for _, r in self.bands_at.get(node, [])}

    def _junction(self, node: str) -> bool:
        """Whether three or more ways, ridable or not, meet at ``node``."""
        return self.junctions.junction(node)

    def movement(self, u: str, v: str, w: str) -> str:
        """``through``, ``left`` or ``right`` for riding u-v then v-w.

        Only a node where three or more ways meet has turns; elsewhere a bend
        is ridden through.
        """
        if not self._junction(v):
            return "through"
        change = self.junctions.change(u, v, w)
        if not abs(change) >= TURN_DEG:  # an unknown heading counts as through
            return "through"
        return "left" if change > 0 else "right"

    def __call__(self, u: str, v: str, w: str) -> CostComponents:
        if not self._junction(v):
            return CostComponents()
        kind = self.movement(u, v, w)
        ridden = str(self.graph[u][v]["physical_edge_id"])
        onto = str(self.graph[v][w]["physical_edge_id"])
        own, road, own_signal = self.edges[ridden]
        turn = self.profile.ratio("turns_per_mi") if kind != "through" else 0.0
        crossing = 0.0
        if not (v in self.signalised or own_signal or self.edges[onto][2]):
            if kind == "left" and own >= 2:  # across the traffic of the road being left
                turn += self.profile.ratio(LEFT_TURN_TERMS[min(own, 3) - 2])
            if kind == "right":  # into the traffic of the road turned onto
                band, onto_road, _ = self.edges[onto]
                if onto_road != road and band > own and band >= 2:
                    crossing = self.profile.ratio("unsignalised_cross_10k_plus_right_turn_per_mi")
            else:
                # The busiest other road met; a count changing along the ridden road is no crossing.
                others = [(b, r) for b, r in self.bands_at.get(v, []) if r != road]
                other = max((b for b, _ in others), default=-1)
                if other > own and other >= 1:
                    crossing = self.profile.ratio(CROSSING_TERMS[other - 1])
                    # Each carriageway of a divided road is half a crossing, so the
                    # road is crossed once however a route passes its two nodes.
                    across = self._across_the_gap(u, ridden) | self._across_the_gap(w, onto)
                    if {r for b, r in others if b == other} <= across:
                        crossing *= 0.5
        return CostComponents(
            turn_cost_s=turn * self.mile_s,
            crossing_cost_s=crossing * self.mile_s,
        )
