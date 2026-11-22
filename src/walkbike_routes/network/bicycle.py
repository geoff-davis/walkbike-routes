"""Generic bicycle LTS classification and routable-link costs."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

import geopandas as gpd

from walkbike_routes.attributes import (
    NONMOTOR_HIGHWAYS,
    bicycle_oneway,
    junction_tag,
    oneway_allows,
    physical_edge_id,
    text_or_none,
)
from walkbike_routes.defaults import (
    BICYCLE_SIGNAL_WAIT_S,
    BICYCLE_SPEED_MPH,
    LTS_COST_FACTORS,
)
from walkbike_routes.models import LinkDirection, Mode
from walkbike_routes.network.base import routable_links_frame
from walkbike_routes.network.grade import parkin_factor
from walkbike_routes.network.signals import SignalWait, SignalWaitInventory
from walkbike_routes.numeric import require_finite, require_finite_settings
from walkbike_routes.routing.costs import CostComponents
from walkbike_routes.units import mph_to_mps

TRAFFIC_ORDER = {
    "no_motor_traffic": 0,
    "very_low": 1,
    "low": 2,
    "moderate": 3,
    "high": 4,
    "very_high": 5,
    "unknown": 6,
}

LTS_FIELDS = {"simplified": "lts_best", "furth": "lts_furth"}
ROAD_DEFAULTS = {
    "motorway": (55, "very_high", 4),
    "motorway_link": (45, "very_high", 2),
    "trunk": (45, "very_high", 4),
    "trunk_link": (40, "high", 2),
    "primary": (35, "high", 4),
    "primary_link": (35, "high", 2),
    "secondary": (35, "high", 2),
    "secondary_link": (30, "moderate", 2),
    "tertiary": (30, "moderate", 2),
    "tertiary_link": (30, "moderate", 2),
    "unclassified": (25, "low", 2),
    "residential": (25, "low", 2),
    "living_street": (15, "very_low", 1),
    "service": (20, "very_low", 1),
}


@dataclass(frozen=True)
class LTSResult:
    """LTS classification with auditable inputs and uncertainty."""

    lts: int
    rules: str
    inputs: str
    missing: str
    inference_dependent: bool


@dataclass(frozen=True)
class BicycleProfile:
    """Bicycle speed and LTS cost factors.

    Every rideable street is kept: traffic stress multiplies riding time by
    its level's factor, a penalty, never a ban.
    """

    profile_id: str
    speed_mph: float = BICYCLE_SPEED_MPH
    lts_cost_factors: tuple[float, float, float, float] = LTS_COST_FACTORS
    unknown_crossing_cost_s: float = 0.0
    uncontrolled_crossing_cost_s: float = 0.0
    signal_wait_s: float = BICYCLE_SIGNAL_WAIT_S
    # Slow riders uphill and speed them downhill (network.grade, Parkin and Rotheram).
    grade_speeds: bool = False
    # Waits across and along a main road at its signals; None uses signal_wait_s.
    signal_wait_cross_arterial_s: float | None = None
    signal_wait_along_arterial_s: float | None = None
    # Which traffic stress levels to read: the rules simplified from Mekuria,
    # Furth and Nixon 2012 (lts_best; classify_lts) or Furth's criteria
    # tables, v2.2 (lts_furth; see network.furth).
    lts_criteria: str = "simplified"

    @property
    def lts_field(self) -> str:
        """The edge column holding this profile's traffic stress levels."""
        return LTS_FIELDS[self.lts_criteria]

    def signal_wait(self, kind: str) -> float:
        """The wait for a :class:`SignalWait` kind."""
        if kind == SignalWait.CROSS_ARTERIAL and self.signal_wait_cross_arterial_s is not None:
            return self.signal_wait_cross_arterial_s
        if kind == SignalWait.ALONG_ARTERIAL and self.signal_wait_along_arterial_s is not None:
            return self.signal_wait_along_arterial_s
        return self.signal_wait_s

    def factor(self, lts: int) -> float:
        if lts not in {1, 2, 3, 4}:
            raise ValueError(f"invalid LTS {lts}")
        return self.lts_cost_factors[lts - 1]

    def __post_init__(self) -> None:
        require_finite_settings(self)
        if self.speed_mph <= 0:
            raise ValueError("bicycle speed must be positive")
        if self.lts_criteria not in LTS_FIELDS:
            raise ValueError(
                f"unknown lts_criteria {self.lts_criteria!r}; choose from: {', '.join(LTS_FIELDS)}"
            )
        if any(factor <= 0 for factor in self.lts_cost_factors):
            raise ValueError("LTS cost factors must be positive")
        if (
            self.unknown_crossing_cost_s < 0
            or self.uncontrolled_crossing_cost_s < 0
            or self.signal_wait_s < 0
            or any(
                w is not None and w < 0
                for w in (self.signal_wait_cross_arterial_s, self.signal_wait_along_arterial_s)
            )
        ):
            raise ValueError("bicycle crossing costs must be nonnegative")


_GROUPED_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")


def parse_number(value: Any) -> float | None:
    """Parse the first ordinary numeric value from common agency fields.

    Grouped thousands separators such as ``12,500`` are read as one number.
    Zero and negative values mean not given.
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text or text in {"nan", "none", "null", "0"} or text.startswith("-"):
        return None
    text = _GROUPED_THOUSANDS.sub("", text)
    token = "".join(
        character if (character.isdigit() or character == ".") else " " for character in text
    ).split()
    if not token:
        return None
    try:
        number = float(token[0])
    except ValueError:
        return None
    return number if number > 0 else None  # "0", "0.0" and 0.0 all mean not given


def infer_traffic_band(
    highway: str | None,
    lanes: float | None,
    speed: float | None,
) -> tuple[str, str]:
    """Return only the categorical traffic information the LTS rules need."""
    highway_value = (highway or "").lower()
    if highway_value in NONMOTOR_HIGHWAYS:
        return "no_motor_traffic", "nonmotor facility"
    if highway_value in {"motorway", "motorway_link", "trunk"}:
        return "very_high", "functional class"
    if (
        (lanes or 0) >= 4
        or (speed or 0) >= 40
        or highway_value in {"primary", "primary_link", "trunk_link"}
    ):
        return "high", "functional class/lanes/speed"
    if (
        (lanes or 0) >= 3
        or (speed or 0) >= 35
        or highway_value in {"secondary", "secondary_link", "tertiary", "tertiary_link"}
    ):
        return "moderate", "functional class/lanes/speed"
    if highway_value in {"residential", "unclassified"}:
        return "low", "functional class"
    if highway_value in {"service", "living_street"}:
        return "very_low", "functional class"
    return "unknown", "insufficient road attributes"


LOW_TRAFFIC_AADT = 3000.0


def counted_traffic_band(inferred: str, aadt: float) -> str:
    """The LTS traffic band of a street with a daily count.

    The LTS criteria (Mekuria, Furth and Nixon 2012; criteria tables, Furth
    2014) use volume once: a street of up to 3 lanes with no marked
    centreline and under 3,000 vehicles a day rates one level lower in mixed
    traffic.  So a count decides only that: under 3,000 is low traffic (or
    very low, if the road class says so), and 3,000 or more is at least
    moderate.  Higher bands still come from road class, lanes and speed.
    """
    if inferred == "no_motor_traffic" or not math.isfinite(aadt) or aadt < 0:
        return inferred
    if aadt < LOW_TRAFFIC_AADT:
        return "very_low" if inferred == "very_low" else "low"
    known = inferred in TRAFFIC_ORDER and inferred != "unknown"
    if known and TRAFFIC_ORDER[inferred] >= TRAFFIC_ORDER["moderate"]:
        return inferred
    return "moderate"


def _with_defaults(
    highway: str,
    speed: float | None,
    traffic: str | None,
    lanes: float | None,
) -> tuple[float, str, float, list[str]]:
    missing: list[str] = []
    if highway in NONMOTOR_HIGHWAYS:
        return speed or 0, traffic or "no_motor_traffic", lanes or 0, missing
    default = ROAD_DEFAULTS.get(highway, (30, "moderate", 2))
    if speed is None:
        missing.append("posted_speed")
        speed = default[0]
    if not traffic or traffic == "unknown":
        missing.append("traffic_volume_band")
        traffic = default[1]
    if lanes is None:
        missing.append("lane_count")
        lanes = default[2]
    return float(speed), traffic, float(lanes), missing


def classify_lts(edge: dict[str, Any]) -> LTSResult:
    """Classify one bicycle edge as LTS 1-4; missing values take the road class's default."""
    highway = str(edge.get("highway") or "").lower()
    if not edge.get("bike_allowed", True):
        return LTSResult(4, "bicycle access prohibited", "bike_allowed=false", "", False)
    speed = parse_number(edge.get("speed_mph"))
    lanes = parse_number(edge.get("lanes"))
    # A missing band arrives as None from dictionaries and as NaN from frame
    # rows; both mean unknown and must fall back to the default.
    traffic = text_or_none(edge.get("traffic_band"))
    volume_type = text_or_none(edge.get("traffic_volume_type"))
    speed, traffic, lanes, missing = _with_defaults(highway, speed, traffic, lanes)
    if volume_type == "inferred traffic band" and traffic != "no_motor_traffic":
        if traffic in {"very_low", "low", "moderate", "high", "very_high"}:
            missing.append("observed_traffic_volume")
    inferred = bool(missing) or volume_type == "inferred traffic band"
    facility = str(edge.get("bike_facility") or "none").lower()
    separated = (
        facility in {"class i", "class iv", "separated path", "protected", "track"}
        or facility.startswith("class i (")
        or facility.startswith("class iv (")
    )
    lane = facility in {"class ii", "bike lane", "lane"} or facility.startswith("class ii (")
    traffic_number = TRAFFIC_ORDER.get(str(traffic), TRAFFIC_ORDER["unknown"])
    inputs = f"highway={highway or 'unknown'}; facility={facility}; speed={speed:g}; traffic={traffic}; lanes={lanes:g}"
    if separated:
        return LTSResult(
            1,
            "physically separated bicycle facility",
            inputs,
            ",".join(missing),
            inferred,
        )
    if highway in NONMOTOR_HIGHWAYS and edge.get("motor_vehicle") not in {"yes", "designated"}:
        return LTSResult(1, "nonmotor path/cycleway", inputs, ",".join(missing), inferred)
    # Limits are read in 5 mph bands, as Furth's tables are: 50 km/h (31.1 mph)
    # is a 30 mph street.
    speed = 5.0 * math.ceil((speed - 3.5) / 5.0)  # 28.5-33.5 is 30, as in furth.MIXED_SPEEDS
    if lane:
        if speed <= 30 and lanes <= 3 and traffic_number <= TRAFFIC_ORDER["moderate"]:
            lts, rule = 2, "bike lane; <=30 mph; <=3 lanes; <=moderate traffic"
        elif speed <= 35 and lanes <= 3 and traffic_number <= TRAFFIC_ORDER["high"]:
            lts, rule = 3, "bike lane but speed/traffic exceeds the LTS 2 thresholds"
        else:
            lts, rule = 4, "bike lane with high speed, lane count, or traffic"
    elif volume_type == "counted AADT" and speed <= 30 and lanes <= 3:
        # Furth's mixed-traffic table: up to 3 lanes, under 3,000 a day is a level lower.
        quiet = traffic_number <= TRAFFIC_ORDER["low"]
        lts = (1 if quiet else 2) if speed <= 25 else (2 if quiet else 3)
        rule = f"counted {'under' if quiet else 'at least'} 3,000/day; <=3 lanes; <={25 if speed <= 25 else 30} mph"
    elif speed <= 25 and lanes <= 2 and traffic_number <= TRAFFIC_ORDER["low"]:
        lts, rule = 1, "low-speed, low-volume, <=2-lane mixed traffic"
    elif speed <= 30 and lanes <= 2 and traffic_number <= TRAFFIC_ORDER["moderate"]:
        lts, rule = 2, "<=30 mph, <=2-lane, <=moderate mixed traffic"
    elif speed <= 35 and lanes <= 3 and traffic_number <= TRAFFIC_ORDER["high"]:
        lts, rule = 3, "moderate/high-stress mixed traffic"
    else:
        lts, rule = 4, "high speed, lane count, or traffic without separation"
    return LTSResult(lts, rule, inputs, ",".join(missing), inferred)


def _crossing_cost(edge: Any, profile: BicycleProfile) -> float:
    if str(getattr(edge, "movement_type", "along")) != "across":
        return 0.0
    state = str(getattr(edge, "crossing_control_state", "unknown")).lower()
    crossing = str(getattr(edge, "crossing", "unknown")).lower()
    if state in {"signal_local", "signal_osm", "controlled_osm", "marked_osm"} or crossing in {
        "traffic_signals",
        "marked",
        "controlled",
        "zebra",
    }:
        return 0.0
    if state == "uncontrolled_osm" or crossing in {"unmarked", "no"}:
        return profile.uncontrolled_crossing_cost_s
    return profile.unknown_crossing_cost_s


def build_bicycle_links(
    physical_edges: gpd.GeoDataFrame,
    profile: BicycleProfile,
    *,
    signal_wait_inventory: SignalWaitInventory | None = None,
    lts_field: str | None = None,
    evidence_source: str = "canonical physical network",
    evidence_confidence: str = "varies by source edge",
) -> gpd.GeoDataFrame:
    """Build directed canonical bicycle links with generalized-second costs.

    ``lts_field`` names the stress column to read; by default the profile's
    (``lts_best``, or ``lts_furth`` for ``lts_criteria: furth``).
    """
    lts_field = lts_field or profile.lts_field
    speed_mps = mph_to_mps(profile.speed_mph)
    require_finite(speed_mps, "speed_mps", positive=True)
    if speed_mps <= 0:
        raise ValueError("bicycle profile speed must be positive")
    rows: list[dict[str, object]] = []
    for raw_edge in physical_edges.itertuples(index=False):
        edge: Any = raw_edge
        if not bool(getattr(edge, "bike_allowed", True)):
            continue
        physical_id = physical_edge_id(edge)
        raw_lts = text_or_none(getattr(edge, lts_field, None))
        if raw_lts is None:
            raise ValueError(f"edge {physical_id} has no {lts_field} value")
        lts = int(float(raw_lts))
        flat_time = float(str(edge.length_m)) / speed_mps
        grade = float(getattr(edge, "grade", math.nan)) if profile.grade_speeds else math.nan
        factor = profile.factor(lts)
        for forward, direction in (
            (True, LinkDirection.FORWARD),
            (False, LinkDirection.REVERSE),
        ):
            if not oneway_allows(bicycle_oneway(edge), junction_tag(edge), forward):
                continue
            # The grade is u to v, so the reverse ride sees -grade.
            movement_time = flat_time / parkin_factor(grade if forward else -grade)
            actual_u = str(edge.u) if forward else str(edge.v)
            actual_v = str(edge.v) if forward else str(edge.u)
            signal_event_id = (
                signal_wait_inventory.event_for_link(
                    u=actual_u,
                    v=actual_v,
                    physical_edge_id=physical_id,
                    include_roadway_signal_entries=True,
                    # A path arriving at a mapped signalised crossing's end:
                    # the crossing charges, not the arrival.
                    skip_clusters_with_crossings=(
                        str(getattr(edge, "highway", "")).lower() in NONMOTOR_HIGHWAYS
                    ),
                )
                if signal_wait_inventory is not None
                else None
            )
            signal_wait = (
                profile.signal_wait(
                    signal_wait_inventory.wait_kind(
                        signal_event_id, u=actual_u, v=actual_v,
                        highway=str(getattr(edge, "highway", "")).lower(),
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
                stress_cost_s=movement_time * (factor - 1),
                crossing_cost_s=_crossing_cost(edge, profile),
            )
            rows.append(
                {
                    "routable_link_id": f"bicycle:{physical_id}:{direction.value}",
                    "physical_edge_id": physical_id,
                    "u": str(edge.u),
                    "v": str(edge.v),
                    "mode": Mode.BICYCLE.value,
                    "direction": direction.value,
                    "link_kind": "bicycle_roadway_or_path",
                    "physical_length_m": float(str(edge.length_m)),
                    **costs.as_link_attributes(),
                    "signal_wait_event_id": signal_event_id,
                    "signal_wait_s": signal_wait,
                    "lts": lts,
                    "evidence_source": evidence_source,
                    "evidence_confidence": evidence_confidence,
                    "geometry": edge.geometry,
                }
            )
    return routable_links_frame(rows, physical_edges.crs)
