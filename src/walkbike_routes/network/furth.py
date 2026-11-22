"""Level of Traffic Stress by Furth's criteria tables, version 2.2 (May 2022).

Furth, P. G. (2022), "Level of Traffic Stress Criteria for Road Segments,
version 2.2", https://peterfurth.sites.northeastern.edu/files/2014/05/LTS-Tables-v2.2.pdf.
The tables cover road segments: bikes in mixed traffic (by lanes, daily
traffic and speed), bike lanes and shoulders not beside parking (by lanes,
bike-lane width and speed) and bike lanes beside parking (by lanes, reach
and speed).  A street with a bike lane takes the lower of its bike-lane and
mixed-traffic levels (the tables' note 4).  Separated tracks and paths are
LTS 1, as in the original criteria (Mekuria, Furth and Nixon 2012, Table 1).
Intersections and crossings are not covered here: the presets' crossing and
signal waits stand in for them.

The tables ask for more than mapped streets usually say.  Where a value is
missing, a stated default is used, and :func:`classify_furth` lists every
default it relied on:

- **Speed**: the posted limit stands in for the prevailing speed; without
  one, the road class default of the legacy rules.
- **Daily traffic**: a matched count (``traffic_aadt``); without one,
  :data:`DEFAULT_ADT` by road class.  These are judgments, set near typical
  counts (counted tertiary roads in Los Altos have a median near 5,800).
- **Lanes**: the busier direction's tagged lanes (``lanes_per_direction``),
  or else the ``lanes`` tag less any centre turn lane (``lanes_both_ways``),
  halved on two-way streets; without a tag, the road class default.
- **Centre line**: ``lane_markings``; without it, streets with a centre
  turn lane and tertiary roads and above have one, and smaller streets do
  not.
- **Parking**: ``bike_lane_parking`` (on the bike lane's side) or
  ``parking_sides``; without them, local and tertiary streets are taken to
  have parking on both sides and busier roads none.
- **Bike-lane width**: ``bike_lane_width_m``, marked buffer included;
  without it, under 6 ft.  Reach (bike lane plus parking lane) needs
  ``bike_lane_reach_m``; without it, it is taken as under 15 ft.
- **Shoulders** count as bike lanes without parking only where their width
  is tagged and at least 4 ft; otherwise the street is mixed traffic.
- **Minimum bike-lane width**: 4 ft, the tables' minimum beside a curb;
  the 3.5 ft minimum beside a road edge is not used, as curbs are not read.
- **Contraflow lanes** are rated by the street's lanes like any bike lane:
  one level serves both directions, and riders going with traffic share it.
- **One-way street width**: ``width_m``; without it, narrow.

Each default leans to the more stressful reading.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from typing import Any

import pandas as pd

from walkbike_routes.attributes import NONMOTOR_HIGHWAYS, text_or_none

SOURCE = "Furth, P. G. (2022), Level of Traffic Stress Criteria for Road Segments, version 2.2"
FT = 0.3048
# Daily traffic assumed where no count is matched, by road class (a judgment).
DEFAULT_ADT = {
    "living_street": 1000, "residential": 1000, "service": 500, "road": 4000,
    "unclassified": 2000, "tertiary": 4000, "tertiary_link": 4000,
    "secondary": 10000, "secondary_link": 10000, "primary": 20000, "primary_link": 20000,
    "trunk": 30000, "trunk_link": 30000, "motorway": 50000, "motorway_link": 30000,
}  # fmt: skip
UNKNOWN_CLASS_ADT = 4000
# Speeds and total lanes by road class where untagged: the legacy rules' defaults.
DEFAULT_SPEED_LANES = {
    "motorway": (55, 4), "motorway_link": (45, 2), "trunk": (45, 4), "trunk_link": (40, 2),
    "primary": (35, 4), "primary_link": (35, 2), "secondary": (35, 2),
    "secondary_link": (30, 2), "tertiary": (30, 2), "tertiary_link": (30, 2),
    "unclassified": (25, 2), "residential": (25, 2), "living_street": (15, 1),
    "service": (20, 1),
}  # fmt: skip
UNKNOWN_CLASS_SPEED_LANES = (30, 2)
CENTRE_LINE_CLASSES = {
    "tertiary", "tertiary_link", "secondary", "secondary_link", "primary", "primary_link",
    "trunk", "trunk_link", "motorway", "motorway_link",
}  # fmt: skip
PARKING_CLASSES = {
    "residential", "unclassified", "living_street", "road", "tertiary", "tertiary_link",
}  # fmt: skip

INF = math.inf
# Mixed traffic: upper speed bounds of each column, then rows of (highest ADT, levels).
MIXED_SPEEDS = (23.5, 28.5, 33.5, 38.5, 43.5, 48.5)
MIXED: dict[str, tuple[tuple[float, tuple[int, ...]], ...]] = {
    "unlaned": (
        (750, (1, 1, 2, 2, 3, 3, 3)),
        (1500, (1, 1, 2, 3, 3, 3, 3)),
        (3000, (2, 2, 2, 3, 3, 4, 4)),
        (INF, (2, 2, 3, 3, 4, 4, 4)),
    ),
    "centre line": (
        (1000, (1, 1, 2, 2, 3, 3, 3)),
        (1500, (2, 2, 2, 3, 3, 4, 4)),
        (INF, (2, 3, 3, 3, 4, 4, 4)),
    ),
    "narrow one-way": (
        (600, (1, 1, 2, 2, 3, 3, 3)),
        (1000, (2, 2, 2, 3, 3, 4, 4)),
        (INF, (2, 3, 3, 3, 4, 4, 4)),
    ),
    "2 lanes": ((8000, (3, 3, 3, 3, 4, 4, 4)), (INF, (3, 3, 4, 4, 4, 4, 4))),
    "3+ lanes": ((INF, (3, 3, 4, 4, 4, 4, 4)),),
}
# Bike lanes and shoulders not beside parking: by lanes per direction and width.
LANE_SPEEDS = (28.5, 33.5, 38.5, 43.5, 48.5)
LANE: dict[tuple[int, bool], tuple[int, ...]] = {
    (1, True): (1, 1, 2, 3, 3, 3),  # (lanes per direction capped at 3, 6 ft or wider)
    (1, False): (2, 2, 2, 3, 3, 4),
    (2, True): (2, 2, 2, 3, 3, 3),
    (2, False): (2, 2, 2, 3, 4, 4),
    (3, True): (3, 3, 3, 4, 4, 4),
    (3, False): (3, 3, 3, 4, 4, 4),
}
# Bike lanes beside parking: by street layout and reach of 15 ft or more.
PARKED_SPEEDS = (28.5, 33.5, 38.5)
PARKED: dict[tuple[str, bool], tuple[int, ...]] = {
    ("1 lane", True): (1, 2, 2, 3),
    ("1 lane", False): (2, 2, 3, 3),
    ("one-way multilane", True): (2, 3, 3, 3),
    ("one-way multilane", False): (3, 3, 3, 3),
    ("2 lanes", True): (2, 3, 3, 3),
    ("2 lanes", False): (3, 3, 3, 3),
    ("other multilane", True): (3, 3, 3, 3),
    ("other multilane", False): (3, 3, 3, 3),
}
MIN_BIKE_LANE_M = 4 * FT  # narrower lanes count as mixed traffic (note 2)
WIDE_BIKE_LANE_M = 6 * FT
MIN_REACH_M = 12 * FT  # bike lane plus parking lane; less counts as mixed traffic
FAR_REACH_M = 15 * FT
TURN_LANE_BONUS_M = 2 * FT  # added on a 1+1 street with a centre turn lane (note 3)
# A one-way street is wide from this width, by sides with parking (0, 1, 2).
WIDE_ONE_WAY_M = (15 * FT, 22 * FT, 30 * FT)


@dataclass(frozen=True)
class FurthResult:
    lts: int
    rule: str
    defaults: tuple[str, ...]


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _level(speed: float, bounds: tuple[float, ...], levels: tuple[int, ...]) -> int:
    return levels[bisect_right(bounds, speed)]


def _mixed(row: str, adt: float, speed: float) -> int:
    for highest, levels in MIXED[row]:
        if adt <= highest:
            return _level(speed, MIXED_SPEEDS, levels)
    raise AssertionError("the last row has no upper bound")  # pragma: no cover


def classify_furth(edge: dict[str, Any]) -> FurthResult:
    """One edge's level of traffic stress by Furth's v2.2 segment tables."""
    highway = (text_or_none(edge.get("highway")) or "").lower()
    allowed = edge.get("bike_allowed", True)
    if allowed is not None and not pd.isna(allowed) and not allowed:
        return FurthResult(4, "bicycle access prohibited", ())
    facility = (text_or_none(edge.get("bike_facility")) or "none").lower()
    if facility in {"track", "separated path", "protected", "class i", "class iv"} or (
        facility.startswith(("class i (", "class iv ("))
    ):
        return FurthResult(1, "physically separated bicycle facility", ())
    if highway in NONMOTOR_HIGHWAYS and edge.get("motor_vehicle") not in {"yes", "designated"}:
        return FurthResult(1, "path without motor traffic", ())
    defaults: list[str] = []
    default_speed, default_lanes = DEFAULT_SPEED_LANES.get(highway, UNKNOWN_CLASS_SPEED_LANES)
    speed = _number(edge.get("speed_mph"))
    if speed is None:
        speed = float(default_speed)
        defaults.append("speed")
    adt = _number(edge.get("traffic_aadt"))
    if adt is None or adt < 0:
        adt = float(DEFAULT_ADT.get(highway, UNKNOWN_CLASS_ADT))
        defaults.append("daily traffic")
    lanes = _number(edge.get("lanes"))
    tagged_turn_lanes = _number(edge.get("lanes_both_ways"))
    turn_lanes = max(tagged_turn_lanes or 0.0, 0.0)
    oneway = (text_or_none(edge.get("oneway")) or "no").lower() in {"yes", "-1", "true", "1"}
    if lanes is None or lanes < 1:
        # The defaults count through lanes both ways: a one-way street has half.
        through = float(max(default_lanes // 2, 1) if oneway else default_lanes)
        defaults.append("lanes")
    else:
        through = max(lanes - turn_lanes, 1.0)
    tagged_per_direction = _number(edge.get("lanes_per_direction"))
    if oneway:
        per_direction = int(through)
    elif tagged_per_direction is not None and tagged_per_direction >= 1:
        per_direction = int(tagged_per_direction)  # lanes:forward / lanes:backward
    elif tagged_turn_lanes is not None:
        # No lane left over for turning: an odd lane goes to the busier direction.
        per_direction = max(math.ceil(int(through) / 2), 1)
    else:
        per_direction = max(int(through) // 2, 1)
    # A two-way street with an odd lane count has a centre turn lane.
    if tagged_turn_lanes is not None:
        centre_turn = turn_lanes > 0
    else:
        centre_turn = not oneway and int(through) % 2 == 1 and through >= 3
    sides = _number(edge.get("parking_sides"))
    if sides is None:
        sides = 2.0 if highway in PARKING_CLASSES else 0.0
        parking_defaulted = True
    else:
        parking_defaulted = False

    if per_direction >= 3:
        row = "3+ lanes"
    elif per_direction == 2:
        row = "2 lanes"
    elif oneway:
        width = _number(edge.get("width_m"))
        if width is None:
            row = "narrow one-way"
            defaults.append("street width")
        else:
            if parking_defaulted:
                defaults.append("parking")
            row = "centre line" if width >= WIDE_ONE_WAY_M[min(int(sides), 2)] else "narrow one-way"
    elif int(through) == 1:
        row = "unlaned"
    else:
        markings = (text_or_none(edge.get("lane_markings")) or "").lower()
        if markings in {"yes", "no"}:
            row = "centre line" if markings == "yes" else "unlaned"
        else:
            # A centre turn lane is marked, so the street has a centre line.
            centre = centre_turn or highway in CENTRE_LINE_CLASSES
            row = "centre line" if centre else "unlaned"
            defaults.append("centre line")
    mixed = _mixed(row, adt, speed)
    mixed_rule = f"mixed traffic, {row}, {adt:,.0f}/day, {speed:g} mph"
    lane = facility in {"lane", "bike lane", "class ii"} or facility.startswith("class ii (")
    shoulder = _number(edge.get("shoulder_width_m"))
    if not lane and shoulder is not None and shoulder >= MIN_BIKE_LANE_M:
        # A paved shoulder of known width is rated as a bike lane without parking.
        return _shoulder(shoulder, per_direction, centre_turn, speed, mixed, mixed_rule, defaults)
    if not lane:
        return FurthResult(mixed, mixed_rule, tuple(defaults))

    # The bike lane's own width, marked buffer included, and the parking beside it.
    width_m = _number(edge.get("bike_lane_width_m"))
    bonus = TURN_LANE_BONUS_M if per_direction == 1 and centre_turn else 0.0
    beside = (text_or_none(edge.get("bike_lane_parking")) or "").lower()
    if beside in {"yes", "no", "some"}:
        tables = {"yes": [True], "no": [False], "some": [True, False]}[beside]
    elif not parking_defaulted and sides in {0.0, 2.0}:
        tables = [sides == 2.0]  # parking on neither side, or on both
    else:
        tables = [highway in PARKING_CLASSES]
        defaults.append("parking")
    if width_m is None:
        defaults.append("bike lane width")
    # Each side is rated on its own table, taking mixed traffic where it is
    # lower (note 4); with parking beside only some lanes, the worse side wins.
    results: list[tuple[int, str]] = []
    for parked in tables:
        if parked:
            results.append(
                _parked(edge, per_direction, oneway, speed, bonus, mixed, mixed_rule, defaults)
            )
        elif width_m is not None and width_m < MIN_BIKE_LANE_M:
            results.append((mixed, f"bike lane under 4 ft; {mixed_rule}"))
        else:
            wide = width_m is not None and width_m + bonus >= WIDE_BIKE_LANE_M
            level = _level(speed, LANE_SPEEDS, LANE[(min(per_direction, 3), wide)])
            rule = (
                f"bike lane {'6 ft or wider' if wide else 'under 6 ft'}, "
                f"{per_direction} lane(s) each way, {speed:g} mph"
            )
            results.append(_lower(level, rule, mixed, mixed_rule))
    lts, rule = max(results)
    return FurthResult(lts, rule, tuple(defaults))


def _shoulder(
    width_m: float,
    per_direction: int,
    centre_turn: bool,
    speed: float,
    mixed: int,
    mixed_rule: str,
    defaults: list[str],
) -> FurthResult:
    bonus = TURN_LANE_BONUS_M if per_direction == 1 and centre_turn else 0.0
    wide = width_m + bonus >= WIDE_BIKE_LANE_M
    level = _level(speed, LANE_SPEEDS, LANE[(min(per_direction, 3), wide)])
    rule = f"shoulder {'6 ft or wider' if wide else 'under 6 ft'}, {per_direction} lane(s) each way, {speed:g} mph"
    lts, rule = _lower(level, rule, mixed, mixed_rule)
    return FurthResult(lts, rule, tuple(defaults))


def _lower(level: int, rule: str, mixed: int, mixed_rule: str) -> tuple[int, str]:
    """The bike-lane level, or mixed traffic where that is lower (note 4)."""
    return (mixed, f"{mixed_rule} (lower than the bike lane)") if mixed < level else (level, rule)


def _parked(
    edge: dict[str, Any],
    per_direction: int,
    oneway: bool,
    speed: float,
    bonus: float,
    mixed: int,
    mixed_rule: str,
    defaults: list[str],
) -> tuple[int, str]:
    """A bike lane beside a parking lane, by layout, reach and speed."""
    if per_direction == 1:
        layout = "1 lane"
    elif oneway:
        layout = "one-way multilane"
    elif per_direction == 2:
        layout = "2 lanes"
    else:
        layout = "other multilane"
    reach_m = _number(edge.get("bike_lane_reach_m"))
    if reach_m is not None:
        if reach_m + bonus < MIN_REACH_M:
            return mixed, f"reach under 12 ft; {mixed_rule}"
        far = reach_m + bonus >= FAR_REACH_M
    else:
        far = False  # reach is rarely mapped: taken as under 15 ft
        defaults.append("bike lane reach")
    level = _level(speed, PARKED_SPEEDS, PARKED[(layout, far)])
    rule = (
        f"bike lane beside parking, {layout}, reach {'15 ft or more' if far else 'under 15 ft'}, "
        f"{speed:g} mph"
    )
    return _lower(level, rule, mixed, mixed_rule)
