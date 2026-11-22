"""Generic pedestrian evidence rules and routable-link costs."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import geopandas as gpd

from walkbike_routes.attributes import NONMOTOR_HIGHWAYS, physical_edge_id, text_or_none
from walkbike_routes.defaults import PEDESTRIAN_SIGNAL_WAIT_S, WALK_SPEED_MPH
from walkbike_routes.models import LinkDirection, Mode
from walkbike_routes.network.base import routable_links_frame
from walkbike_routes.network.grade import tobler_factor
from walkbike_routes.network.signals import SignalWait, SignalWaitInventory
from walkbike_routes.numeric import require_finite, require_finite_settings
from walkbike_routes.routing.costs import CostComponents
from walkbike_routes.units import mph_to_mps

LOCAL_HIGHWAYS = {"living_street", "residential", "service", "unclassified"}
COLLECTOR_HIGHWAYS = {"tertiary", "tertiary_link"}
MAJOR_HIGHWAYS = {
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "trunk",
    "trunk_link",
}

_MARKED_CROSSING_MARKING_BASES = frozenset({"dots", "ladder", "lines", "surface", "yes", "zebra"})
_UNMARKED_CROSSING_MARKINGS = frozenset({"no", "none", "unmarked"})


def crossing_markings_indicate_marked(value: Any) -> bool:
    """Return whether an OSM ``crossing:markings`` value is affirmative.

    OSM uses ``no`` for an explicitly unmarked crossing.  Merely testing that
    the tag is present therefore reverses the meaning of the most important
    negative value.  Compound affirmative values such as ``lines;zebra:double``
    are accepted by their base token; missing and unfamiliar values remain
    non-affirmative.
    """
    if not isinstance(value, str):
        return False
    tokens = (token.strip().split(":", maxsplit=1)[0] for token in value.lower().split(";"))
    return any(token in _MARKED_CROSSING_MARKING_BASES for token in tokens)


def crossing_markings_indicate_unmarked(value: Any) -> bool:
    """Return whether an OSM ``crossing:markings`` value is explicitly negative."""
    return isinstance(value, str) and value.strip().lower() in _UNMARKED_CROSSING_MARKINGS


@dataclass(frozen=True)
class PedestrianProfile:
    """Pedestrian speed and facility-evidence disutility."""

    profile_id: str
    speed_mph: float = WALK_SPEED_MPH
    allow_unknown_local: bool = True
    allow_unknown_collector: bool = False
    allow_unknown_major: bool = False
    unknown_local_factor: float = 1.25
    unknown_collector_factor: float = 2.0
    unknown_other_factor: float = 2.0
    unknown_major_factor: float = 5.0
    signal_wait_s: float = PEDESTRIAN_SIGNAL_WAIT_S
    # Slow walkers on hills by Tobler's hiking function (network.grade).
    grade_speeds: bool = False
    # Waits across and along a main road at its signals; None uses signal_wait_s.
    signal_wait_cross_arterial_s: float | None = None
    signal_wait_along_arterial_s: float | None = None
    # Charge links with a known missing sidewalk or poor crossing their class's
    # unknown factor, instead of leaving them out.
    allow_problematic: bool = False

    def signal_wait(self, kind: str) -> float:
        """The wait for a :class:`SignalWait` kind."""
        if kind == SignalWait.CROSS_ARTERIAL and self.signal_wait_cross_arterial_s is not None:
            return self.signal_wait_cross_arterial_s
        if kind == SignalWait.ALONG_ARTERIAL and self.signal_wait_along_arterial_s is not None:
            return self.signal_wait_along_arterial_s
        return self.signal_wait_s

    def __post_init__(self) -> None:
        require_finite_settings(self)
        for factor in (
            self.unknown_local_factor,
            self.unknown_collector_factor,
            self.unknown_other_factor,
            self.unknown_major_factor,
        ):
            require_finite(factor, "pedestrian cost factor", positive=True)
        if self.speed_mph <= 0:
            raise ValueError("pedestrian profile speed must be positive")
        waits = (
            self.signal_wait_s, self.signal_wait_cross_arterial_s, self.signal_wait_along_arterial_s
        )  # fmt: skip
        if any(w is not None and w < 0 for w in waits):
            raise ValueError("pedestrian signal wait must be nonnegative")


def pedestrian_status(edge: dict[str, Any]) -> tuple[str, str]:
    """Return supported, problematic, or unknown pedestrian evidence."""
    if not edge.get("ped_allowed", True):
        return "problematic", "pedestrian access prohibited"
    movement = edge.get("movement_type", "along")
    crossing = str(edge.get("crossing") or "").lower()
    crossing_control = str(edge.get("crossing_control_state") or "").lower()
    curb_ramp = str(edge.get("curb_ramp_state") or "").lower()
    highway = str(edge.get("highway") or "").lower()
    if movement == "across":
        if crossing_control in {
            "signal_local",
            "signal_osm",
            "controlled_osm",
            "marked_osm",
        }:
            ramp_note = (
                "; local curb-ramp evidence nearby" if curb_ramp == "installed_local" else ""
            )
            return "supported", f"positive crossing-control evidence: {crossing_control}{ramp_note}"
        if crossing in {"traffic_signals", "marked", "controlled", "zebra", "pelican"}:
            return "supported", f"mapped {crossing} crossing"
        if crossing_control == "uncontrolled_osm" or crossing in {"unmarked", "no"}:
            return "problematic", f"mapped {crossing} crossing"
        if curb_ramp == "known_absent_local":
            return (
                "problematic",
                "local curb-ramp inventory records no installed ramp; crossing control unknown",
            )
        if curb_ramp == "installed_local":
            return (
                "unknown",
                "local curb ramp is present, but crossing control/markings remain unknown",
            )
        return "unknown", "crossing control/markings and curb-ramp support unknown"
    sidewalk = edge.get("sidewalk_state")
    if highway in NONMOTOR_HIGHWAYS:
        return "supported", "mapped pedestrian path"
    if sidewalk in {"present_manual", "present_local", "present_osm"}:
        return "supported", f"positive sidewalk evidence: {sidewalk}"
    if sidewalk == "absent_manual":
        return "problematic", "manual field review records sidewalk absent"
    if sidewalk == "absent_osm_explicit" and highway in {"primary", "secondary", "tertiary"}:
        return "problematic", "OSM explicitly records no sidewalk on a higher-order road"
    return "unknown", "no complete sidewalk evidence"


def evidence_factor(profile: PedestrianProfile, status: str, highway: str) -> float | None:
    """Return the distance factor for a link, or ``None`` when the profile excludes it.

    Unknown-sidewalk roadways are gated by class: quiet local streets,
    tertiary collectors, and major roads each have their own switch, so a
    profile that admits local streets does not silently admit collectors.
    Paths, crossings, and unrecognized classes with unknown evidence follow the
    local switch at the ``unknown_other_factor``.  Problematic links (a
    sidewalk known to be missing) are left out unless ``allow_problematic``,
    when they cost their class's unknown factor whatever the switches.
    """
    if status == "supported":
        return 1.0
    if status == "problematic":
        if not profile.allow_problematic:
            return None
        if highway in LOCAL_HIGHWAYS:
            return profile.unknown_local_factor
        if highway in COLLECTOR_HIGHWAYS:
            return profile.unknown_collector_factor
        if highway in MAJOR_HIGHWAYS:
            return profile.unknown_major_factor
        return profile.unknown_other_factor
    if highway in LOCAL_HIGHWAYS:
        return profile.unknown_local_factor if profile.allow_unknown_local else None
    if highway in COLLECTOR_HIGHWAYS:
        return profile.unknown_collector_factor if profile.allow_unknown_collector else None
    if highway in MAJOR_HIGHWAYS:
        return profile.unknown_major_factor if profile.allow_unknown_major else None
    return profile.unknown_other_factor if profile.allow_unknown_local else None


def _no_crossing(edge: Any) -> bool:
    """A crossing link recorded as ``crossing=no``: crossing is not allowed there."""
    movement = text_or_none(getattr(edge, "movement_type", None)) or ""
    crossing = text_or_none(getattr(edge, "crossing", None)) or ""
    return movement.lower() == "across" and crossing.lower() == "no"


def build_pedestrian_links(
    physical_edges: gpd.GeoDataFrame,
    profile: PedestrianProfile,
    *,
    signal_wait_inventory: SignalWaitInventory | None = None,
    evidence_source: str = "canonical physical network",
    evidence_confidence: str = "varies by source edge",
) -> gpd.GeoDataFrame:
    """Build walking links from evidence status.

    A link serves both directions, unless a signal wait applies in only one
    (entering a signalized intersection), when each direction gets its own link.
    """
    speed_mps = mph_to_mps(profile.speed_mph)
    require_finite(speed_mps, "speed_mps", positive=True)
    if speed_mps <= 0:
        raise ValueError("pedestrian profile speed must be positive")
    rows: list[dict[str, object]] = []
    for raw_edge in physical_edges.itertuples(index=False):
        edge: Any = raw_edge
        if not bool(getattr(edge, "ped_allowed", True)):
            continue
        if _no_crossing(edge):
            continue
        physical_id = physical_edge_id(edge)
        status = str(getattr(edge, "pedestrian_status", "unknown"))
        highway = str(getattr(edge, "highway", ""))
        factor = evidence_factor(profile, status, highway)
        if factor is None:
            continue
        flat_time = float(str(edge.length_m)) / speed_mps
        grade = float(getattr(edge, "grade", math.nan)) if profile.grade_speeds else math.nan
        # Walking time each way: the grade is u to v, so the reverse walk sees -grade.
        times = {
            forward: flat_time / tobler_factor(grade if forward else -grade)
            for forward in (True, False)
        }
        # A walker entering a signalized intersection along a road pays one wait
        # there; paths and footways do not.  Arriving at a mapped crosswalk's end,
        # the crosswalk charges instead.  A road whose sidewalks are mapped as separate ways is
        # the carriageway itself: walking it into or inside such an intersection
        # pays as crossing it, so it is no way round the crosswalks' waits.
        along_road = (
            highway.lower() not in NONMOTOR_HIGHWAYS
            and str(getattr(edge, "movement_type", "along")) == "along"
        )
        in_carriageway = along_road and str(getattr(edge, "sidewalk", "")) == "separate"
        events = {
            forward: (
                signal_wait_inventory.event_for_link(
                    u=str(edge.u) if forward else str(edge.v),
                    v=str(edge.v) if forward else str(edge.u),
                    physical_edge_id=physical_id,
                    include_roadway_signal_entries=along_road,
                    skip_clusters_with_crossings=not in_carriageway,
                    charge_inside_crossing_clusters=in_carriageway,
                )
                if signal_wait_inventory is not None
                else None
            )
            for forward in (True, False)
        }
        # One link serves both directions unless they wait or take different times.
        directions = (
            [(LinkDirection.BOTH, events[True], times[True], f"pedestrian:{physical_id}")]
            if events[True] == events[False] and times[True] == times[False]
            else [
                (direction, events[forward], times[forward],
                 f"pedestrian:{physical_id}:{direction.value}")
                for forward, direction in ((True, LinkDirection.FORWARD),
                                           (False, LinkDirection.REVERSE))
            ]
        )  # fmt: skip
        for direction, signal_event_id, movement_time, link_id in directions:
            signal_wait = (
                profile.signal_wait(
                    signal_wait_inventory.wait_kind(
                        signal_event_id, u=str(edge.u), v=str(edge.v),
                        # In the carriageway of an intersection with mapped
                        # crosswalks: charged as crossing the main road.
                        highway=""
                        if in_carriageway
                        and signal_event_id in signal_wait_inventory.clusters_with_crossings
                        else highway.lower(),
                        crossing_road=str(getattr(edge, "crossing_road", "") or ""),
                    )
                )
                if signal_event_id is not None and signal_wait_inventory is not None
                else 0.0
            )  # fmt: skip
            costs = CostComponents(
                movement_time_s=movement_time,
                crossing_wait_time_s=signal_wait,
                travel_time_s=movement_time + signal_wait,
                facility_cost_s=movement_time * (factor - 1),
            )
            rows.append(
                {
                    "routable_link_id": link_id,
                    "physical_edge_id": physical_id,
                    "u": str(edge.u),
                    "v": str(edge.v),
                    "mode": Mode.PEDESTRIAN.value,
                    "direction": direction.value,
                    "link_kind": "pedestrian_roadway_or_path",
                    "physical_length_m": float(str(edge.length_m)),
                    **costs.as_link_attributes(),
                    "signal_wait_event_id": signal_event_id,
                    "signal_wait_s": signal_wait,
                    "pedestrian_status": status,
                    "evidence_source": evidence_source,
                    "evidence_confidence": evidence_confidence,
                    "geometry": edge.geometry,
                }
            )
    return routable_links_frame(rows, physical_edges.crs)
