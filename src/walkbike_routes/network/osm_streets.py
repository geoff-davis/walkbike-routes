"""Turn an Overpass answer into the street table that ``network.prepare`` reads.

Ways are split wherever they meet another way, and at signals and stop signs,
so every intersection and controlled point is a node.  Tags are read the way
OpenStreetMap documents them:

- ``maxspeed`` without a unit is in km/h; ``"25 mph"`` is in mph.
- ``cycleway``, ``cycleway:both``, ``cycleway:left`` and ``cycleway:right``
  give the bike facility: a track beats a lane beats nothing.
- ``sidewalk`` and its ``:both``, ``:left`` and ``:right`` forms give the
  sidewalk; footways and paths are paths themselves.
- ``access``, ``foot`` and ``bicycle`` decide who may use a way.
- A crossing way (``footway=crossing`` and the like) is a signalised crossing
  when it is tagged ``crossing=traffic_signals`` or ``crossing:signals=yes``,
  or one of its nodes is a signal and the way is not tagged as having none.  It becomes an ``across`` link, with the
  class of the busiest road it meets in ``crossing_road``.  Other crossings
  stay ordinary paths.  Every crossing way, signalised or not, records the
  road it crosses in ``crossed_road`` and its paint, ``marked``,
  ``unmarked`` or unknown, in ``crossing_marking``.
- ``footway``, ``service`` and ``surface`` are kept as tagged, for the
  walking route-choice model (network.broach_walk).
- ``bridge`` and ``tunnel`` mark ways whose height is not the ground's.
- ``red_turn:right`` on a signal node (``red_turn:right:bicycle`` first)
  says whether a right turn on red is allowed there, in ``right_on_red``:
  ``no``, ``yes`` or blank when not tagged.

Coordinates stay in degrees; the caller projects the result.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

import geopandas as gpd
from shapely.geometry import LineString, Point

from walkbike_routes.attributes import (
    NO_CARS,
    NO_CYCLING,
    NO_WALKING_OR_CYCLING,
    NONMOTOR_HIGHWAYS,
)
from walkbike_routes.network.pedestrian import (
    crossing_markings_indicate_marked,
    crossing_markings_indicate_unmarked,
)

KMH_TO_MPH = 0.621371
# Ways that can carry a pedestrian, cyclist or car.  Construction, proposed,
# raceways, platforms and the like are left out.
HIGHWAYS = frozenset(
    {
        "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
        "secondary", "secondary_link", "tertiary", "tertiary_link", "unclassified",
        "residential", "living_street", "service", "road", "pedestrian", "footway",
        "path", "cycleway", "steps", "track", "bridleway",
    }
)  # fmt: skip
PATHS = NONMOTOR_HIGHWAYS
ALLOWED = {"yes", "designated", "permissive", "official"}
REFUSED = {"no", "private"}
YES = {"yes", "true", "1"}

LINK_COLUMNS = [
    "link_id",
    "u",
    "v",
    "osm_way_id",
    "name",
    "highway",
    "lanes",
    "speed_mph",
    "bike_facility",
    "sidewalk",
    "oneway",
    "oneway_bicycle",
    "bike_allowed",
    "ped_allowed",
    "car_allowed",
    "movement_type",
    "crossing",
    "crossing_road",
    "structure",
    "lane_markings",
    "lanes_both_ways",
    "lanes_per_direction",
    "shoulder_width_m",
    "parking_sides",
    "bike_lane_parking",
    "bike_lane_reach_m",
    "bike_lane_width_m",
    "width_m",
    "footway",
    "service",
    "surface",
    "crossed_road",
    "crossing_marking",
    "geometry",
]
# Road classes from busiest to quietest, to name the road a crossing crosses.
ROAD_RANK = [
    "motorway", "trunk", "primary", "secondary", "motorway_link", "trunk_link",
    "primary_link", "secondary_link", "tertiary", "tertiary_link",
    "unclassified", "residential", "living_street", "road", "service",
]  # fmt: skip


def streets(answer: dict[str, Any]) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Links and nodes, in degrees, from a raw Overpass answer."""
    points: dict[int, tuple[float, float]] = {}
    node_tags: dict[int, dict[str, str]] = {}
    ways: list[dict[str, Any]] = []
    for element in answer.get("elements", []):
        if element.get("type") == "node":
            points[int(element["id"])] = (float(element["lon"]), float(element["lat"]))
            if element.get("tags"):
                node_tags[int(element["id"])] = element["tags"]
        elif element.get("type") == "way" and element.get("tags", {}).get("highway") in HIGHWAYS:
            ways.append(element)
    ways = [way for way in ways if all(int(n) in points for n in way["nodes"])]
    controls = {node: control(tags) for node, tags in node_tags.items()}
    # A node a way passes twice (a figure eight) is a junction with itself.
    use = Counter(int(n) for way in ways for n in way["nodes"])
    roads: dict[int, set[str]] = {}
    for way in ways:
        if way["tags"]["highway"] in ROAD_RANK:
            for n in way["nodes"]:
                roads.setdefault(int(n), set()).add(way["tags"]["highway"])
    rows: list[dict[str, Any]] = []
    joined: set[frozenset[int]] = set()
    for way in sorted(ways, key=lambda w: int(w["id"])):
        refs = [int(n) for n in way["nodes"]]
        attributes = way_attributes(way["tags"])
        attributes.update(crossing_attributes(way["tags"], refs, controls, roads, node_tags))
        sections: list[list[int]] = []
        start = 0
        for index in range(1, len(refs)):
            node = refs[index]
            last = index == len(refs) - 1
            if not (last or use[node] > 1 or controls.get(node, "none") != "none"):
                continue
            sections.extend(_open_pieces(refs[start : index + 1]))
            start = index
        for piece, part in enumerate(_distinct(sections, joined)):
            rows.append(
                {
                    "link_id": f"w{way['id']}-{piece}",
                    "u": f"n{part[0]}",
                    "v": f"n{part[-1]}",
                    "osm_way_id": int(way["id"]),
                    **attributes,
                    "geometry": LineString([points[n] for n in part]),
                }
            )
    links = gpd.GeoDataFrame(rows, columns=LINK_COLUMNS, geometry="geometry", crs=4326)
    used = sorted({int(x[1:]) for x in (*links.u, *links.v)})
    nodes = gpd.GeoDataFrame(
        {
            "node_id": [f"n{n}" for n in used],
            "control": [controls.get(n, "none") for n in used],
            # A crosswalk's paint where it is tagged on a road node (network.broach_walk).
            "crossing_marking": [
                crossing_marking(node_tags[n]) if n in node_tags else "" for n in used
            ],
            "right_on_red": [right_on_red(node_tags.get(n, {})) for n in used],
        },
        geometry=[Point(points[n]) for n in used],
        crs=4326,
    )
    return links, nodes


def _open_pieces(section: list[int]) -> list[list[int]]:
    """A section, or a closed loop cut in three so its pieces join distinct pairs."""
    if section[0] != section[-1]:
        return [section]
    if len(set(section)) < 3:
        return []  # out and back over the same node: nothing to route on
    n = len(section) - 1  # segments; at least 3 here
    first, second = n // 3, 2 * n // 3
    return [section[: first + 1], section[first : second + 1], section[second:]]


def _distinct(sections: list[list[int]], joined: set[frozenset[int]]) -> list[list[int]]:
    """Sections cut so that no two links join the same pair of nodes.

    The routing graph keeps one link per pair of nodes, so a second link
    between the same two junctions (the far half of a loop, or a parallel
    street) is cut at an inner node.  The inner node is not a junction, so
    both new pairs are unused.  A link with no inner node is left as it is.
    """
    result: list[list[int]] = []
    for section in sections:
        pair = frozenset((section[0], section[-1]))
        if pair in joined and len(section) > 2:
            middle = len(section) // 2
            parts = [section[: middle + 1], section[middle:]]
        else:
            parts = [section]
        for part in parts:
            joined.add(frozenset((part[0], part[-1])))
            result.append(part)
    return result


def crossing_attributes(
    tags: dict[str, str],
    refs: list[int],
    controls: dict[int, str],
    roads: dict[int, set[str]],
    node_tags: dict[int, dict[str, str]] | None = None,
) -> dict[str, str]:
    """``movement_type``, ``crossing``, ``crossing_road``, ``crossed_road`` and
    ``crossing_marking`` for one way."""
    along = {
        "movement_type": "along", "crossing": "", "crossing_road": "",
        "crossed_road": "", "crossing_marking": "",
    }  # fmt: skip
    crossing_way = tags["highway"] in PATHS and (
        "crossing" in {tags.get("footway"), tags.get("cycleway"), tags.get("path")}
        or any(key == "crossing" or key.startswith("crossing:") for key in tags)
    )
    if not crossing_way:
        return along
    met = set().union(*(roads.get(n, set()) for n in refs))
    crossed = min(met, key=ROAD_RANK.index) if met else ""
    # From the way and its nodes on the road it crosses: an explicit "unmarked" wins.
    found = {crossing_marking(tags)} | {
        crossing_marking((node_tags or {}).get(n, {})) for n in refs if n in roads
    }
    marking = "unmarked" if "unmarked" in found else "marked" if "marked" in found else ""
    along = {**along, "crossed_road": crossed, "crossing_marking": marking}
    # "unmarked" is about paint, so a signal may still be mapped there; "no"
    # (no crossing allowed) and "uncontrolled" rule a signal out unless
    # crossing:signals says otherwise.
    explicit = tags.get("crossing") == "traffic_signals" or tags.get("crossing:signals") == "yes"
    ruled_out = (
        tags.get("crossing") in {"no", "uncontrolled"} or tags.get("crossing:signals") == "no"
    )
    if not explicit and (ruled_out or not any(controls.get(n) == "signal" for n in refs)):
        return along
    return {**along, "movement_type": "across", "crossing": "traffic_signals",
            "crossing_road": crossed}  # fmt: skip


MARKED_CROSSINGS = frozenset({"marked", "zebra", "uncontrolled", "traffic_signals"})


def crossing_marking(tags: dict[str, str]) -> str:
    """``marked``, ``unmarked`` or ``""`` (unknown) for a crossing way.

    ``crossing:markings`` decides where tagged.  Otherwise ``crossing=unmarked``
    is unmarked, and ``marked``, ``zebra``, ``uncontrolled`` (a marked crossing
    without signals, in OpenStreetMap's usage), ``traffic_signals`` or a
    ``crossing_ref`` of ``zebra`` are marked.
    """
    markings = tags.get("crossing:markings")
    if crossing_markings_indicate_unmarked(markings):
        return "unmarked"
    if crossing_markings_indicate_marked(markings):
        return "marked"
    kind = tags.get("crossing", "")
    if kind == "unmarked":
        return "unmarked"
    if kind in MARKED_CROSSINGS or tags.get("crossing_ref") == "zebra":
        return "marked"
    return ""


def right_on_red(tags: dict[str, str]) -> str:
    """``no`` or ``yes`` where a node says whether bikes may turn right on red, else blank."""
    for key in ("red_turn:right:bicycle", "red_turn:right"):
        value = str(tags.get(key, "")).strip().lower()
        if value in {"no", "yes"}:
            return value
    return ""


def control(tags: dict[str, str]) -> str:
    """``signal``, ``stop`` or ``none`` for a tagged node."""
    if tags.get("traffic_signals") == "ramp_meter":
        return "none"  # meters cars joining a motorway; nobody else waits at it
    if (
        tags.get("highway") == "traffic_signals"
        or tags.get("crossing") == "traffic_signals"
        or tags.get("crossing:signals") == "yes"
    ):
        return "signal"
    if tags.get("highway") == "stop":
        return "stop"
    return "none"


def way_attributes(tags: dict[str, str]) -> dict[str, Any]:
    """The street-table columns for one way's tags."""
    highway = tags["highway"]
    return {
        "name": tags.get("name", ""),
        "highway": highway,
        "lanes": first_number(tags.get("lanes")),
        "speed_mph": speed_mph(tags.get("maxspeed")),
        "bike_facility": bike_facility(tags),
        "sidewalk": sidewalk(tags),
        "oneway": oneway(tags),
        "oneway_bicycle": oneway_bicycle(tags),
        "bike_allowed": allowed(tags, "bicycle"),
        "ped_allowed": allowed(tags, "foot"),
        "car_allowed": car_allowed(tags),
        "structure": structure(tags),
        # For Furth's LTS tables (network.furth); usually untagged.
        "lane_markings": tags.get("lane_markings", "")
        if tags.get("lane_markings") in {"yes", "no"}
        else "",
        **directional_lanes(tags),
        "parking_sides": parking_sides(tags),
        **bike_lane_side(tags),
        "shoulder_width_m": shoulder_width_m(tags),
        "width_m": first_length_m(tags, ("width",)),
        # For the walking route-choice model (network.broach_walk).
        "footway": tags.get("footway", ""),
        "service": tags.get("service", ""),
        "surface": tags.get("surface", ""),
    }


MIN_BIKE_LANE_M = 4 * 0.3048  # Furth's minimum beside a curb (network.furth)
MIN_REACH_M = 12 * 0.3048  # bike lane plus parking lane
NO_PARKING = {"no", "no_parking", "no_stopping", "separate", "fire_lane", "none"}


def shoulder_width_m(tags: dict[str, str]) -> float | None:
    """A paved shoulder's width in metres, when the way has one and its width is tagged."""
    sides = ("right", "both", "left")
    if any(tags.get(k) == "shoulder" for k in ("cycleway", *(f"cycleway:{s}" for s in sides))):
        keys = ("cycleway:right:width", "cycleway:both:width", "cycleway:width")
        width = first_length_m(
            tags, (*keys, "shoulder:right:width", "shoulder:both:width", "shoulder:width")
        )
        if width is not None:
            return width
    if tags.get("shoulder") in {"yes", "both", "right", "left"}:
        return first_length_m(
            tags, ("shoulder:right:width", "shoulder:both:width", "shoulder:width")
        )
    return None


def directional_lanes(tags: dict[str, str]) -> dict[str, float | None]:
    """Through lanes in the busier direction, and the centre turn lanes.

    ``lanes:forward`` and ``lanes:backward`` give the first; with both and a
    ``lanes`` total, whatever is left over is a centre turn lane, unless
    ``lanes:both_ways`` says otherwise.  With one direction tagged and the
    total known, the other direction has what is left.
    """
    forward = first_number(tags.get("lanes:forward"))
    backward = first_number(tags.get("lanes:backward"))
    both_ways = first_number(tags.get("lanes:both_ways"))
    total = first_number(tags.get("lanes"))
    if both_ways is None and None not in (forward, backward, total):
        assert forward is not None and backward is not None and total is not None
        both_ways = max(total - forward - backward, 0.0)
    if total is not None and (forward is None) != (backward is None):
        # One direction tagged: the other has the lanes left over (all of them
        # through lanes unless lanes:both_ways says otherwise).
        known_side = forward if forward is not None else backward or 0.0
        rest = max(total - (both_ways or 0.0) - known_side, 0.0)
        forward, backward = (forward, rest) if forward is not None else (rest, backward)
    known = [n for n in (forward, backward) if n is not None]
    return {"lanes_both_ways": both_ways, "lanes_per_direction": max(known) if known else None}


def parking_sides(tags: dict[str, str]) -> float | None:
    """How many sides allow parking (0, 1 or 2), or None if that is not tagged.

    Reads both the current ``parking:<side>`` scheme and the older
    ``parking:lane:<side>``.  A side left untagged counts as no parking only
    when the other side has parking; if nothing says parking, it is unknown.
    """
    values = []
    for side in ("left", "right"):
        value = None
        for scheme in ("parking", "parking:lane"):
            value = value or tags.get(f"{scheme}:{side}") or tags.get(f"{scheme}:both")
        values.append(value)
    present = [v is not None and v not in NO_PARKING for v in values]
    if any(present):
        return float(sum(present))
    if all(v is not None for v in values):
        return 0.0
    return None


def _lane_sides(tags: dict[str, str]) -> list[str]:
    """The sides of the street with a painted bike lane; a side's own tag wins.

    A plain ``cycleway`` tag covers both sides of a two-way street and the
    right of a one-way one (the left of a reversed one), going with traffic.
    """
    lanes = {"lane", "opposite_lane"}
    plain: set[str] = set()
    if tags.get("cycleway") in lanes:
        plain = {"yes": {"right"}, "-1": {"left"}}.get(oneway(tags), {"left", "right"})
    sides = []
    for side in ("left", "right"):
        value = tags.get(f"cycleway:{side}", tags.get("cycleway:both"))
        if value in lanes or (value is None and side in plain):
            sides.append(side)
    return sides


def bike_lane_side(tags: dict[str, str]) -> dict[str, Any]:
    """The bike lane's width (with any marked buffer), parking beside it, and reach.

    Each side with a bike lane is read on its own and the worst side wins; a
    side without a width tag makes the width unknown.  ``bike_lane_parking``
    is ``yes`` when a side with a bike lane has parking, ``no`` when every
    such side is tagged without it, ``some`` when sides differ, and empty if
    unknown.
    ``bike_lane_reach_m`` is the bike lane plus parking lane on the parked
    sides, when every one of them has both widths tagged.
    """
    widths: list[float | None] = []
    reaches: list[float | None] = []
    beside: list[str | None] = []
    for side in _lane_sides(tags) or ["right"]:
        keys = (f"cycleway:{side}", "cycleway:both", "cycleway")
        width = first_length_m(tags, tuple(f"{k}:width" for k in keys))
        if width is not None:
            width += first_length_m(tags, tuple(f"{k}:buffer" for k in keys)) or 0.0
        widths.append(width)
        value = None
        for scheme in ("parking", "parking:lane"):
            value = value or tags.get(f"{scheme}:{side}") or tags.get(f"{scheme}:both")
        beside.append(value)
        if value is not None and value not in NO_PARKING:
            parked = first_length_m(tags, (f"parking:{side}:width", "parking:both:width"))
            reaches.append(None if width is None or parked is None else width + parked)
    has_parking = [v is not None and v not in NO_PARKING for v in beside]
    if any(has_parking) and not all(has_parking):
        parking = "some"  # rated on both tables, the worse winning; untagged sides too
    elif any(has_parking):
        parking = "yes"
    elif beside and all(v is not None for v in beside):
        parking = "no"
    else:
        parking = ""
    # The width feeds the no-parking table, so it is read on the sides without
    # parking (all sides if every one has it).  One unknown side makes it
    # unknown, unless a known side is already under the 4 ft minimum.
    unparked = [w for w, parked in zip(widths, has_parking, strict=True) if not parked]
    rated = unparked if unparked else widths
    known_widths = [w for w in rated if w is not None]
    narrowest = min(known_widths) if known_widths else None
    if len(known_widths) < len(rated) and (narrowest is None or narrowest >= MIN_BIKE_LANE_M):
        narrowest = None
    known_reaches = [r for r in reaches if r is not None]
    shortest = min(known_reaches) if known_reaches else None
    if len(known_reaches) < len(reaches) and (shortest is None or shortest >= MIN_REACH_M):
        shortest = None  # unknown, unless a known side is already under 12 ft
    return {
        "bike_lane_width_m": narrowest,
        "bike_lane_parking": parking,
        "bike_lane_reach_m": shortest,
    }


def length_m(value: str | None) -> float | None:
    """A width tag in metres: bare numbers are metres; ``ft`` or ``'`` mark feet."""
    if not value:
        return None
    text = value.strip().lower()
    feet = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:'|ft|feet)\s*(?:(\d+(?:\.\d+)?)\s*(?:\"|in))?", text)
    if feet:
        return float(feet.group(1)) * 0.3048 + float(feet.group(2) or 0) * 0.0254
    metres = re.fullmatch(r"(\d+(?:\.\d+)?)\s*m?", text)
    return float(metres.group(1)) if metres else None


def first_length_m(tags: dict[str, str], keys: tuple[str, ...]) -> float | None:
    """The first of ``keys`` that holds a readable width, in metres."""
    for key in keys:
        width = length_m(tags.get(key))
        if width is not None:
            return width
    return None


def structure(tags: dict[str, str]) -> str:
    """``bridge``, ``tunnel`` or empty: where the ground is not the way's height."""
    for kind in ("bridge", "tunnel"):
        if tags.get(kind, "no") not in {"no", ""}:
            return kind
    if tags.get("covered") == "yes" and tags.get("layer", "0").startswith("-"):
        return "tunnel"
    return ""


def first_number(value: str | None) -> float | None:
    """The first number in a tag such as ``"2"`` or ``"2;3"``."""
    if not value:
        return None
    match = re.search(r"\d+(?:\.\d+)?", value)
    return float(match.group()) if match else None


def speed_mph(value: str | None) -> float | None:
    """A ``maxspeed`` tag in mph; bare numbers are km/h, as OSM defines them."""
    if not value:
        return None
    text = value.strip().lower()
    number = None if text.startswith("-") else first_number(text.split(";")[0])
    if number is None:
        return None  # "none", "signals", "US:urban" and the like
    if "mph" in text:
        return number if number > 0 else None
    if number <= 0:
        return None  # no limit that a street can have
    if "knots" in text:
        return round(number * 1.15078, 1)
    return round(number * KMH_TO_MPH, 1)


def bike_facility(tags: dict[str, str]) -> str:
    """``path``, ``track``, ``lane``, ``boulevard`` or ``none``."""
    highway = tags["highway"]
    if highway == "cycleway":
        return "path"
    if highway in {"path", "footway", "pedestrian", "track"} and tags.get("bicycle") in {
        "designated",
        "yes",
    }:
        return "path"
    values = {
        tags.get(key, "")
        for key in ("cycleway", "cycleway:both", "cycleway:left", "cycleway:right")
    }
    if values & {"track", "opposite_track"}:
        return "track"
    if values & {"lane", "opposite_lane"}:
        return "lane"
    if tags.get("bicycle_road") == "yes" or tags.get("cyclestreet") == "yes":
        return "boulevard"
    return "none"


def sidewalk(tags: dict[str, str]) -> str:
    """``path``, ``both``, ``left``, ``right``, ``separate``, ``none`` or ``unknown``."""
    if tags["highway"] in PATHS:
        return "path"
    both = tags.get("sidewalk:both")
    left = tags.get("sidewalk:left", both)
    right = tags.get("sidewalk:right", both)
    main = tags.get("sidewalk")
    if main in {"both", "left", "right", "separate"}:
        return main
    if main in {"no", "none"}:
        return "none"
    if main == "yes":
        return "both"
    sides = {"left": left, "right": right}
    if "separate" in sides.values():
        return "separate"
    present = [side for side, value in sides.items() if value == "yes"]
    if len(present) == 2:
        return "both"
    if present:
        return present[0]
    if left == "no" and right == "no":
        return "none"
    return "unknown"


def oneway(tags: dict[str, str]) -> str:
    """``yes``, ``-1``, ``no`` or ``reversible`` for cars.

    Roundabouts and motorways are one way.  A street whose direction changes
    by time of day (``reversible``, ``alternating``) is ``reversible``: closed
    both ways, since no run knows which way it is open.
    """
    value = tags.get("oneway", "").lower()
    if value in YES:
        return "yes"
    if value in {"-1", "reverse"}:
        return "-1"
    if value == "no":
        return "no"
    if value in {"reversible", "alternating"}:
        return "reversible"
    if tags.get("junction") in {"roundabout", "circular"} or tags["highway"] == "motorway":
        return "yes"
    return "no"


def oneway_bicycle(tags: dict[str, str]) -> str | None:
    """A bicycle exception to the one-way rule, or None to follow it.

    ``oneway:bicycle`` decides when it is ``yes``, ``-1`` or ``no``; any other
    value is ignored.  Contraflow lanes and tracks (``cycleway=opposite*`` or
    a ``cycleway:*:oneway=-1``) mean bikes may ride both ways.
    """
    value = tags.get("oneway:bicycle", "").lower()
    if value == "no":
        return "no"
    if value in YES:
        return "yes"
    if value in {"-1", "reverse"}:
        return "-1"
    if any(
        tags.get(k, "").startswith("opposite")
        for k in ("cycleway", "cycleway:left", "cycleway:right")
    ):
        return "no"
    if any(
        tags.get(f"cycleway:{side}:oneway") in {"-1", "no"} for side in ("left", "right", "both")
    ):
        return "no"
    return None


def allowed(tags: dict[str, str], mode: str) -> bool:
    """Whether walkers (``foot``) or cyclists (``bicycle``) may use a way."""
    highway = tags["highway"]
    own = tags.get(mode)
    if own in ALLOWED:
        return True
    if own in REFUSED or own == "use_sidepath":
        return False
    if mode == "bicycle":  # a bicycle is a vehicle
        vehicle = tags.get("vehicle")
        if vehicle in ALLOWED:
            return True
        if vehicle in REFUSED:
            return False
    if highway in (NO_CYCLING if mode == "bicycle" else NO_WALKING_OR_CYCLING):
        return False
    return tags.get("access") not in REFUSED


def car_allowed(tags: dict[str, str]) -> bool:
    """Whether cars may use a way; the most specific access tag decides."""
    for key in ("motorcar", "motor_vehicle", "vehicle"):
        if tags.get(key) in REFUSED:
            return False
        if tags.get(key) in ALLOWED:
            return True
    if tags["highway"] in NO_CARS:
        return False
    return tags.get("access") not in REFUSED
