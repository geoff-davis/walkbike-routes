"""Walking route choice from Broach's Portland GPS model.

Source: Broach, J. (2016), "Travel mode choice framework incorporating
realistic bike and walk routes", PhD dissertation, Portland State
University, Table 10: a path-size logit estimated on 1,167 walk trip stages
by 283 Portland adults, 2010-2013, with variables defined in Table 9.  The
same model was presented as Broach and Dill (2015).

Route utility is linear: ``b_d distance + sum_k b_k x_k + b_ps ln(path
size)``, where each ``x_k`` is a distance along links of some kind (steep
upslope, alley or unpaved, busy street) or a count (turns, crossings).  Each
term is therefore worth a fixed distance, ``b_k / b_d`` metres per count or
per metre, exactly, with no approximation.  Costs are this equivalent
distance at the profile's flat walking speed, in seconds, and the logit
scale follows: ``1 / (|b_d| * speed)`` seconds.

The model's terms, mapped to this tool's network (all assumptions):

- **steep upslope**: links climbing at 10% or more, in the direction walked;
- **substandard street**: alleys (``service=alley``) and streets with an
  unpaved ``surface``; paths are not streets, and the model found no effect
  of paths;
- **busy street**: tertiary roads and above (the paper: collector or higher,
  about 10,000+ vehicles a day), and sidewalks mapped beside them
  (``beside_road``, see :mod:`walkbike_routes.network.sidewalks`), since the
  model was estimated on centre lines;
- **turns**: at a node where three or more ways meet, a change of heading of
  30 degrees or more onto a street of a different name.  A sidewalk takes
  its street's name; an unnamed way is a street of its own, except that
  unnamed busy roads of one class are one road.  Stepping onto or off a
  crossing way is not a turn;
- **unsignalised arterial crossing**: crossing a secondary road or above
  without a signal; **unmarked collector crossing**: crossing a tertiary
  road with neither a marked crosswalk nor a signal.  A mapped crossing way
  charges its own crossing (pieces of one crossing share it by length), and
  counts as marked unless it or its node on the road is tagged unmarked.  Elsewhere, at a node without a signal,
  walking on from one street to another (or straight on) across a road of
  a third name crosses it, unmarked unless the node is tagged as a marked
  crossing;
- **neighborhood commercial** frontage is not known here and is left out;
- **traveling together** applies only with ``together: true``.

Two optional settings are not in the paper, for the child preset
(``walk/broach-2016-child``): ``stress_scale`` multiplies the busy-street and
crossing terms, and the ``unknown_*_factor`` settings make a link without
sidewalk evidence count as that many times its length, by road class as in
:class:`~walkbike_routes.network.pedestrian.PedestrianProfile`.  Both
default to the model as printed.

The model has no term for sidewalks: the paper tested missing sidewalks and
found no effect, perhaps because Portland's alleys and unpaved streets, which
lack them, took it.  So no walkable link is penalised for its sidewalk
evidence, and links known to lack one are walked.  It has no term for
signal delay either: signal waits stay in travel time, so time limits are as
for the other presets, but not in the cost.
"""

from __future__ import annotations

import dataclasses
import functools
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import geopandas as gpd
import networkx as nx
import pandas as pd

from walkbike_routes.defaults import PEDESTRIAN_SIGNAL_WAIT_S, WALK_SPEED_MPH
from walkbike_routes.models import LinkDirection
from walkbike_routes.network.base import costs_are_valid, routable_links_frame
from walkbike_routes.network.broach import MEDIAN_LINK_M
from walkbike_routes.network.pedestrian import (
    COLLECTOR_HIGHWAYS,
    MAJOR_HIGHWAYS,
    PedestrianProfile,
    build_pedestrian_links,
    evidence_factor,
)
from walkbike_routes.network.signals import SignalWaitInventory, build_signal_wait_inventory
from walkbike_routes.numeric import require_finite_settings
from walkbike_routes.routing.costs import CostComponents
from walkbike_routes.routing.turns import Junctions
from walkbike_routes.units import mph_to_mps

# Coefficient names, after Table 10.  "_m" terms are per metre of the route.
COEFFICIENTS = frozenset(
    {
        "distance_m", "distance_together_m", "turns", "steep_upslope_m", "substandard_m",
        "busy_m", "commercial_m", "unsignalised_arterial_crossings",
        "unmarked_collector_crossings", "ln_path_size",
    }
)  # fmt: skip
# The terms a profile's stress_scale multiplies: busy streets and crossings.
STRESS_TERMS = frozenset(
    {"busy_m", "unsignalised_arterial_crossings", "unmarked_collector_crossings"}
)
STEEP_GRADE = 0.10
TURN_DEG = 30.0
BUSY_HIGHWAYS = COLLECTOR_HIGHWAYS | MAJOR_HIGHWAYS
ARTERIAL_HIGHWAYS = MAJOR_HIGHWAYS
STREETS = frozenset(
    {"living_street", "residential", "service", "unclassified", "road"} | BUSY_HIGHWAYS
)
UNPAVED = frozenset(
    {
        "unpaved", "gravel", "fine_gravel", "compacted", "dirt", "earth", "ground", "grass",
        "sand", "mud", "pebblestone", "woodchips", "rock",
    }
)  # fmt: skip
SIGNAL_STATES = frozenset({"signal_local", "signal_osm"})


@dataclass(frozen=True)
class WalkBroachProfile:
    """Speed, waits and the published coefficients."""

    profile_id: str
    coefficients: Mapping[str, float] = field(default_factory=dict)
    together: bool = False
    speed_mph: float = WALK_SPEED_MPH
    signal_wait_s: float = PEDESTRIAN_SIGNAL_WAIT_S
    signal_wait_cross_arterial_s: float | None = None
    signal_wait_along_arterial_s: float | None = None
    # Slow walkers on hills in travel time (network.grade); the cost is the model's.
    grade_speeds: bool = True
    # Not in the paper (see module notes): a multiplier on STRESS_TERMS, and
    # distance factors for links without sidewalk evidence, by road class as
    # in PedestrianProfile.  The defaults leave the model as printed.
    stress_scale: float = 1.0
    unknown_local_factor: float = 1.0
    unknown_collector_factor: float = 1.0
    unknown_other_factor: float = 1.0
    unknown_major_factor: float = 1.0

    def __post_init__(self) -> None:
        require_finite_settings(self)
        missing = sorted(COEFFICIENTS - set(self.coefficients))
        extra = sorted(set(self.coefficients) - COEFFICIENTS)
        if missing or extra:
            raise ValueError(f"coefficients: missing {missing}, unknown {extra}")
        if not all(math.isfinite(float(b)) for b in self.coefficients.values()):
            raise ValueError("coefficients must be finite")
        if self.distance_coefficient >= 0:
            raise ValueError("the distance coefficient must be negative")
        if self.coefficients["ln_path_size"] < 0:
            raise ValueError("the path-size coefficient must not be negative")
        if self.speed_mph <= 0:
            raise ValueError("walking speed must be positive")
        if self.stress_scale <= 0:
            raise ValueError("stress_scale must be positive")
        if min(self._unknown_factors) < 1:
            raise ValueError("sidewalk evidence factors must be at least 1")

    @property
    def _unknown_factors(self) -> tuple[float, float, float, float]:
        return (
            self.unknown_local_factor, self.unknown_collector_factor,
            self.unknown_other_factor, self.unknown_major_factor,
        )  # fmt: skip

    @property
    def values_sidewalks(self) -> bool:
        """Whether links without sidewalk evidence cost more than their length."""
        return max(self._unknown_factors) > 1

    @functools.cached_property
    def evidence(self) -> PedestrianProfile:
        """The profile whose evidence factors this one adds (every link walkable)."""
        return dataclasses.replace(
            self.timing(),
            unknown_local_factor=self.unknown_local_factor,
            unknown_collector_factor=self.unknown_collector_factor,
            unknown_other_factor=self.unknown_other_factor,
            unknown_major_factor=self.unknown_major_factor,
        )

    @property
    def distance_coefficient(self) -> float:
        """Utility per metre walked."""
        b = float(self.coefficients["distance_m"])
        return b + float(self.coefficients["distance_together_m"]) if self.together else b

    def metres(self, name: str) -> float:
        """Term ``name`` in metres of walking: per count, or per metre for ``_m`` terms."""
        scale = self.stress_scale if name in STRESS_TERMS else 1.0
        return scale * float(self.coefficients[name]) / self.distance_coefficient

    @property
    def speed_mps(self) -> float:
        return mph_to_mps(self.speed_mph)

    @property
    def logit_scale_s(self) -> float:
        """The logit scale on costs in seconds at the flat speed."""
        return 1.0 / (-self.distance_coefficient * self.speed_mps)

    def timing(self) -> PedestrianProfile:
        """The plain profile that gives travel time and signal waits: every walkable link."""
        return PedestrianProfile(
            self.profile_id,
            speed_mph=self.speed_mph,
            unknown_local_factor=1.0,
            unknown_collector_factor=1.0,
            unknown_other_factor=1.0,
            unknown_major_factor=1.0,
            signal_wait_s=self.signal_wait_s,
            signal_wait_cross_arterial_s=self.signal_wait_cross_arterial_s,
            signal_wait_along_arterial_s=self.signal_wait_along_arterial_s,
            grade_speeds=self.grade_speeds,
        )


def _missing(value: Any) -> bool:
    return value is None or (pd.api.types.is_scalar(value) and bool(pd.isna(value)))


def _text(value: Any) -> str:
    return "" if _missing(value) else str(value).strip().lower()


def _number(value: Any, default: float) -> float:
    try:
        number = math.nan if _missing(value) else float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def substandard(edge: Mapping[str, Any]) -> bool:
    """An alley, or a street with an unpaved surface."""
    highway = _text(edge.get("highway"))
    if highway not in STREETS:
        return False
    if highway == "service" and _text(edge.get("service")) == "alley":
        return True
    surface = _text(edge.get("surface")).split(";")[0].strip()
    return surface in UNPAVED


def busy(edge: Mapping[str, Any]) -> bool:
    """Along a tertiary road or above, or a sidewalk beside one."""
    if _text(edge.get("movement_type")) == "across" or _text(edge.get("crossed_road")):
        return False
    return (
        _text(edge.get("highway")) in BUSY_HIGHWAYS
        or _text(edge.get("beside_road")) in BUSY_HIGHWAYS
    )


def link_share(profile: WalkBroachProfile, edge: Mapping[str, Any], grade: float) -> float:
    """The share of its own length a link adds, walked at ``grade``."""
    share = 0.0
    if math.isfinite(grade) and grade >= STEEP_GRADE:
        share += profile.metres("steep_upslope_m")
    if substandard(edge):
        share += profile.metres("substandard_m")
    if busy(edge):
        share += profile.metres("busy_m")
    if profile.values_sidewalks:
        status = _text(edge.get("pedestrian_status")) or "unknown"
        factor = evidence_factor(profile.evidence, status, _text(edge.get("highway")))
        share += (factor or 1.0) - 1.0
    return share


def signalised_crossing(edge: Mapping[str, Any], signal_legs: frozenset[str] = frozenset()) -> bool:
    """Whether a crossing way has a signal."""
    return (
        _text(edge.get("crossing")) == "traffic_signals"
        or _text(edge.get("crossing_control_state")) in SIGNAL_STATES
        or str(edge.get("physical_edge_id")) in signal_legs
    )


def crossing_metres(
    profile: WalkBroachProfile, edge: Mapping[str, Any], signal_legs: frozenset[str] = frozenset()
) -> float:
    """The crossing term a mapped crossing way (or its piece) carries, in metres."""
    road = _text(edge.get("crossed_road")) or _text(edge.get("crossing_road"))
    if not road or signalised_crossing(edge, signal_legs):
        return 0.0
    share = _number(edge.get("crossing_share"), 1.0)
    if road in ARTERIAL_HIGHWAYS:
        return profile.metres("unsignalised_arterial_crossings") * share
    if road in COLLECTOR_HIGHWAYS and _text(edge.get("crossing_marking")) == "unmarked":
        return profile.metres("unmarked_collector_crossings") * share
    return 0.0


def build_walk_broach_links(
    physical_edges: gpd.GeoDataFrame,
    profile: WalkBroachProfile,
    *,
    signal_wait_inventory: SignalWaitInventory | None = None,
    nodes: gpd.GeoDataFrame | None = None,
) -> gpd.GeoDataFrame:
    """Walking links costed by Broach's (2016) model.

    Without ``signal_wait_inventory``, it is built from ``nodes`` when given.
    """
    if signal_wait_inventory is None and nodes is not None:
        signal_wait_inventory = build_signal_wait_inventory(nodes, physical_edges)
    base = build_pedestrian_links(
        physical_edges, profile.timing(), signal_wait_inventory=signal_wait_inventory
    )
    if base.empty:
        return base
    legs = (
        frozenset(signal_wait_inventory.crossing_leg_edges)
        if signal_wait_inventory
        else frozenset()
    )
    edges: dict[str, dict[str, Any]] = {
        str(row["physical_edge_id"]): {str(k): v for k, v in row.items()}
        for row in physical_edges.drop(columns="geometry").to_dict("records")
    }
    # A signal on any piece of a crossing drawn in pieces covers the whole crossing.
    groups: dict[str, set[str]] = {}
    for edge_id, edge in edges.items():
        if group := _text(edge.get("crossing_group")):
            groups.setdefault(group, set()).add(edge_id)
    legs = legs | frozenset(
        edge_id
        for members in groups.values()
        if any(signalised_crossing(edges[m], legs) for m in members)
        for edge_id in members
    )
    rows: list[dict[str, Any]] = []
    for raw in base.to_dict("records"):
        row = {str(k): v for k, v in raw.items()}
        edge = edges[str(row["physical_edge_id"])]
        grade = _number(edge.get("grade"), math.nan)
        direction = str(row["direction"])
        # A link serving both ways splits where the climb differs between them.
        if direction == LinkDirection.BOTH and (
            link_share(profile, edge, grade) != link_share(profile, edge, -grade)
        ):
            split = [
                {**row, "direction": LinkDirection.FORWARD.value,
                 "routable_link_id": f"{row['routable_link_id']}:forward"},
                {**row, "direction": LinkDirection.REVERSE.value,
                 "routable_link_id": f"{row['routable_link_id']}:reverse"},
            ]  # fmt: skip
        else:
            split = [row]
        for link in split:
            walked = -grade if str(link["direction"]) == LinkDirection.REVERSE else grade
            flat = float(link["physical_length_m"]) / profile.speed_mps
            link["stress_cost_s"] = flat * link_share(profile, edge, walked)
            link["facility_cost_s"] = 0.0
            link["crossing_cost_s"] = crossing_metres(profile, edge, legs) / profile.speed_mps
            # The cost is the model's equivalent distance at the flat speed:
            # hills' slowing and signal waits stay in travel time only.
            link["control_cost_s"] = flat - float(link["travel_time_s"])
            link["generalized_cost_s"] = (
                float(link["travel_time_s"]) + link["stress_cost_s"] + link["crossing_cost_s"]
                + link["control_cost_s"] + float(link.get("turn_cost_s", 0.0) or 0.0)
                + float(link.get("transition_cost_s", 0.0) or 0.0)
            )  # fmt: skip
            link["link_kind"] = "pedestrian_broach_2016"
            rows.append(link)
    out = routable_links_frame(rows, physical_edges.crs)
    if not costs_are_valid(out):
        raise ValueError(
            "a Broach walking link cost came out negative, or zero on a link with length"
        )
    return out


def _road(edge: Mapping[str, Any], edge_id: str) -> str:
    """Which street an edge is part of: its name, its street's for a sidewalk, or itself.

    Unnamed busy roads of one class count as one road, so that turning onto
    one does not also cross its other leg.
    """
    named = _text(edge.get("beside_name")) or _text(edge.get("name"))
    if named:
        return named
    highway = _text(edge.get("beside_road")) or _text(edge.get("highway"))
    return f"unnamed {highway}" if highway in BUSY_HIGHWAYS else f"edge {edge_id}"


class WalkBroachMovements:
    """The model's turn and crossing terms for each movement ``u-v-w`` at a node.

    Pass it to :func:`~walkbike_routes.routing.turns.turn_graph`.  See the
    module notes for how movements are classified.
    """

    def __init__(
        self,
        graph: nx.DiGraph[str],
        physical_edges: gpd.GeoDataFrame,
        nodes: gpd.GeoDataFrame,
        profile: WalkBroachProfile,
        *,
        signal_wait_inventory: SignalWaitInventory | None = None,
    ) -> None:
        if signal_wait_inventory is None:
            signal_wait_inventory = build_signal_wait_inventory(nodes, physical_edges)
        self.graph = graph
        self.profile = profile
        self.junctions = Junctions(graph, physical_edges, nodes)
        self.signalised = set(signal_wait_inventory.cluster_by_node)
        legs = frozenset(signal_wait_inventory.crossing_leg_edges)
        if "control" in nodes:
            self.signalised |= set(
                nodes.node_id.astype(str)[nodes.control.astype(str).eq("signal")]
            )
        # Nodes where a marked crosswalk is tagged on the road itself.
        self.marked: set[str] = set()
        if "crossing_marking" in nodes:
            marked = nodes.crossing_marking.fillna("").astype(str).eq("marked")
            self.marked = set(nodes.node_id.astype(str)[marked])
        # edge id -> (street, crossing way, signalised)
        self.edges: dict[str, tuple[str, bool, bool]] = {}
        self.median_links = set(
            physical_edges.physical_edge_id.astype(str)[
                (physical_edges.geometry.length <= MEDIAN_LINK_M).to_numpy()
            ]
        )
        self.roads_at: dict[str, list[tuple[str, str]]] = {}
        for raw in physical_edges.drop(columns="geometry").to_dict("records"):
            row = {str(k): v for k, v in raw.items()}
            edge_id = str(row["physical_edge_id"])
            road = _road(row, edge_id)
            crossing_way = bool(_text(row.get("crossed_road"))) or (
                _text(row.get("movement_type")) == "across"
            )
            self.edges[edge_id] = (road, crossing_way, signalised_crossing(row, legs))
            highway = _text(row.get("highway"))
            if highway in BUSY_HIGHWAYS:
                for node in (str(row["u"]), str(row["v"])):
                    self.roads_at.setdefault(node, []).append((highway, road))

    def turns(self, u: str, v: str, w: str) -> bool:
        """Whether walking u-v then v-w is a turn, in the model's sense."""
        if not self.junctions.junction(v):
            return False
        here, crossing_here, _ = self.edges[str(self.graph[u][v]["physical_edge_id"])]
        onto, crossing_onto, _ = self.edges[str(self.graph[v][w]["physical_edge_id"])]
        if crossing_here or crossing_onto or here == onto:
            return False
        return abs(self.junctions.change(u, v, w)) >= TURN_DEG  # NaN is no turn

    def crossed(self, u: str, v: str, w: str) -> str:
        """The busiest road crossed at ``v`` without a signal, or ``""``."""
        here, crossing_here, signal_here = self.edges[str(self.graph[u][v]["physical_edge_id"])]
        onto, crossing_onto, signal_onto = self.edges[str(self.graph[v][w]["physical_edge_id"])]
        if crossing_here or crossing_onto or v in self.signalised or signal_here or signal_onto:
            return ""
        if not self.junctions.junction(v):
            return ""
        met = [highway for highway, road in self.roads_at.get(v, []) if road not in {here, onto}]
        if any(highway in ARTERIAL_HIGHWAYS for highway in met):
            return "arterial"
        if any(highway in COLLECTOR_HIGHWAYS for highway in met) and v not in self.marked:
            return "collector"
        return ""

    def _one_carriageway(self, u: str, v: str, w: str, kind: str) -> bool:
        """Whether the road crossed at ``v`` is a divided road's one carriageway.

        Its other carriageway is at the far end of a short link ridden to or
        from ``v``.  Each is half a crossing, so the road is crossed once
        however a route passes its two nodes.
        """
        classes = ARTERIAL_HIGHWAYS if kind == "arterial" else COLLECTOR_HIGHWAYS
        here = self.edges[str(self.graph[u][v]["physical_edge_id"])][0]
        onto = self.edges[str(self.graph[v][w]["physical_edge_id"])][0]
        crossed = {
            road
            for highway, road in self.roads_at.get(v, [])
            if highway in classes and road not in {here, onto}
        }
        across: set[str] = set()
        for node, a, b in ((u, u, v), (w, v, w)):
            if str(self.graph[a][b]["physical_edge_id"]) in self.median_links:
                across |= {road for _, road in self.roads_at.get(node, [])}
        return bool(crossed) and crossed <= across

    def __call__(self, u: str, v: str, w: str) -> CostComponents:
        metres_s = 1.0 / self.profile.speed_mps
        turn = self.profile.metres("turns") if self.turns(u, v, w) else 0.0
        kind = self.crossed(u, v, w)
        crossing = (
            self.profile.metres("unsignalised_arterial_crossings")
            if kind == "arterial"
            else self.profile.metres("unmarked_collector_crossings")
            if kind == "collector"
            else 0.0
        )
        if kind and self._one_carriageway(u, v, w, kind):
            crossing *= 0.5
        return CostComponents(turn_cost_s=turn * metres_s, crossing_cost_s=crossing * metres_s)
