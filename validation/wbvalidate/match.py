"""Which modelled street each count site is on, and its flow under each run.

A site matches the links within :data:`RADIUS_M` whose street has the site's
road name.  Names are compared after tidying: case, punctuation, direction
words (North, S) and street types (Street and St are one word).  A sidewalk
counts as the street beside it, as in the report's busiest-streets tables;
crossings never match, since counters sit mid-block, nor do links the
counted mode may not use.  A trail or path count
whose name matches nothing takes the nearest path or cycleway within the
radius.

The modelled flow is read across the street at the counter: a line through
the site, square to the nearest matched link, crosses each matched link
once (a divided road's two carriageways, or a street and its sidewalks).

- A cycling count of both directions is the sum over the roadways and paths
  crossed (not sidewalks), so both halves of a divided road count.
- A count of one direction takes only the nearest carriageway, and is set
  aside unless that is one way for the mode (a walking count of one
  direction always is, since walking flows add both).
- A walking count of one side takes the nearest sidewalk crossed, or the
  street when sidewalks are not mapped apart; a walking count with no side
  is the sum over everything crossed.
- A path count matched as the nearest path takes that one path.

Sites are set aside, with the reason, when they

- lie outside the comparison area (the study boundary, so every compared
  site is at least the study's buffer from the network's edge);
- match no street;
- count one direction of a street that is two-way for that mode, since the
  modelled flow adds both directions.

Nothing is dropped silently: every site appears in the output with a status.
"""

from __future__ import annotations

import math
import re

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points

from walkbike_routes.report.tables import is_crossing_link, street_names

RADIUS_M = 25.0
MATCHED = "matched"
OUTSIDE = "outside the comparison area"
NO_STREET = "no street of that name within 25 m"
ONE_DIRECTION = "one direction of a two-way street"
PATHS = {"path", "cycleway", "footway", "pedestrian", "bridleway", "track"}
TRAIL_WORDS = ("trail", "path", "multiuse", "multi-use", "greenway", "sidepath")
STREET_TYPES = {
    "street": "st", "st": "st", "avenue": "ave", "ave": "ave", "av": "ave",
    "road": "rd", "rd": "rd", "boulevard": "blvd", "blvd": "blvd", "drive": "dr",
    "dr": "dr", "lane": "ln", "ln": "ln", "place": "pl", "pl": "pl", "parkway": "pkwy",
    "pkwy": "pkwy", "pky": "pkwy", "court": "ct", "ct": "ct", "terrace": "ter",
    "ter": "ter", "highway": "hwy", "hwy": "hwy", "square": "sq", "sq": "sq",
    "trail": "trl", "trl": "trl", "pike": "pk", "pk": "pk", "expressway": "expy",
    "expy": "expy", "bridge": "br", "br": "br", "walk": "wk", "plaza": "plz",
}  # fmt: skip
DIRECTIONS = {"north", "south", "east", "west", "n", "s", "e", "w", "ne", "nw", "se", "sw"}


def street_key(name: str) -> str:
    """A street name tidied for comparing: "North 6th Street" and "6th st" agree.

    Direction words go only where a name is left ("South Street" stays as it
    is), and so do the "Northbound Lanes" that label one half of a divided road.
    """
    text = re.sub(r"[^a-z0-9 ]+", " ", name.lower().replace("'", ""))
    text = re.sub(r"\b(north|south|east|west)bound\b|\blanes?\b", " ", text)
    words = [STREET_TYPES.get(w, w) for w in text.split()]
    named = [w for w in words if w not in DIRECTIONS]
    return " ".join(named if set(named) - set(STREET_TYPES.values()) else words)


def same_street(site: str, link: str) -> bool:
    """Whether two tidied names are one street, allowing "j f kennedy" for "john f kennedy"
    and a bridge named for its street."""
    if not site or not link:
        return False
    if site == link or site.removesuffix(" br") == link or link.removesuffix(" br") == site:
        return True
    a, b = site.split(), link.split()
    return (
        len(a) == len(b)
        and all(
            x == y or (len(x) == 1 and y.startswith(x)) or (len(y) == 1 and x.startswith(y))
            for x, y in zip(a, b, strict=True)
        )
        and a[-1] == b[-1]
    )


def match(
    counts: gpd.GeoDataFrame,
    links: gpd.GeoDataFrame,
    flows: dict[str, pd.Series],
    *,
    within: BaseGeometry | None = None,
    radius_m: float = RADIUS_M,
) -> gpd.GeoDataFrame:
    """Each site with its status, matched links and ``flow:<run>`` for each run.

    ``flows`` maps a run name (``walk-legacy``) to flow by physical edge id;
    a site takes flows only from runs of its own mode.  ``within`` is the
    comparison area, in the links' CRS.  ``link_ids`` are all the matched
    links; ``section`` the groups whose flows are added, one per carriageway,
    taking the highest flow within a group (a line through a node meets the
    links on both sides of it).
    """
    sites = counts.to_crs(str(links.crs)).reset_index(drop=True)
    keys = street_names(links)
    tidy = {link: street_key(_label(key)) for link, key in keys.items()}
    table = links[["link_id", "highway", "geometry"]].copy()
    table["link_id"] = table.link_id.astype(str)
    table["street"] = table.link_id.map(tidy).fillna("")
    table["crossing"] = is_crossing_link(links).to_numpy()
    for column in ("bike_facility", "oneway", "oneway_bicycle", "footway"):
        table[column] = links[column].fillna("").astype(str).values if column in links else ""
    for mode, column in ACCESS.items():  # unknown access counts as allowed
        table[f"{mode}_ok"] = links[column].ne(False).to_numpy() if column in links else True
    table["line"] = table.geometry
    near = gpd.sjoin(
        sites[["geometry"]].assign(site=sites.index),
        table.assign(geometry=table.geometry.buffer(radius_m)),
        predicate="within",
    )
    rows = []
    for index, site in sites.iterrows():
        usable = near[f"{site['mode']}_ok"].astype(bool)
        candidates = near[near.site.eq(int(str(index))) & ~near.crossing & usable]
        rows.append(_one(site, candidates, within, radius_m))
    found = pd.DataFrame(rows, columns=list(_EMPTY))
    out = sites.join(found)
    for run, flow in flows.items():
        mode = run.split("-", 1)[0]
        values = flow.astype(float).to_dict()
        out[f"flow:{run}"] = [
            sum(max(values.get(link, 0.0) for link in group) for group in section)
            if status == MATCHED and site_mode == mode
            else np.nan
            for section, status, site_mode in zip(out.section, out.status, out["mode"], strict=True)
        ]
    return gpd.GeoDataFrame(out, geometry="geometry", crs=links.crs)


_EMPTY: dict[str, object] = {"status": NO_STREET, "matched_by": "", "link_ids": (),
                             "section": (), "highway": "", "bike_facility": ""}  # fmt: skip
NOT_FOR_BIKES = {"footway", "pedestrian", "steps"}
ACCESS = {"bike": "bike_allowed", "walk": "ped_allowed"}


def _one(
    site: pd.Series, candidates: pd.DataFrame, within: BaseGeometry | None, radius_m: float
) -> dict[str, object]:
    if within is not None and not site.geometry.within(within):
        return {**_EMPTY, "status": OUTSIDE}
    road = street_key(str(site.road)) if str(site.road).strip() else ""
    chosen = candidates[candidates.street.map(lambda link: same_street(road, link))]
    how = "name"
    trail = any(word in f"{site.road} {site.facility}".lower() for word in TRAIL_WORDS)
    if chosen.empty and (trail or not road):
        chosen = candidates[candidates.highway.isin(PATHS)]
        how = "nearest path"
    if chosen.empty:
        return dict(_EMPTY)
    chosen = chosen.assign(away_m=chosen.line.distance(site.geometry))
    mode = str(site["mode"])
    if how == "nearest path":
        groups = [_nearest(chosen)]
    else:
        groups = _cross_section(site.geometry, chosen, radius_m)
        if mode == "bike":
            groups = [g for g in groups if not _for_walkers(g).all()] or groups
        elif str(site.side).strip():
            sidewalks = [g for g in groups if g.footway.eq("sidewalk").any()]
            groups = [min(sidewalks or groups, key=lambda g: g.away_m.min())]
    one_direction = site.direction != "both"
    if one_direction:
        groups = [min(groups, key=lambda g: g.away_m.min())]
    used = pd.concat(groups)
    best = _main_link(used)
    found: dict[str, object] = {
        "status": MATCHED,
        "matched_by": how,
        "link_ids": tuple(sorted(set(chosen.link_id))),
        "section": tuple(tuple(sorted(set(g.link_id))) for g in groups),
        "highway": str(best.highway),
        "bike_facility": str(best.bike_facility),
    }
    if one_direction and not _one_way(used, mode):
        found["status"] = ONE_DIRECTION
    return found


def _nearest(chosen: pd.DataFrame) -> pd.DataFrame:
    return chosen.iloc[[int(np.argmin(chosen.away_m.to_numpy()))]]


def _for_walkers(group: pd.DataFrame) -> pd.Series:
    return group.highway.isin(NOT_FOR_BIKES) | group.footway.eq("sidewalk")


def _cross_section(point: Point, chosen: pd.DataFrame, radius_m: float) -> list[pd.DataFrame]:
    """The matched links a line square to the nearest one crosses, grouped by where."""
    nearest = _nearest(chosen)
    line = nearest.line.iloc[0]
    at = line.project(point)
    a, b = line.interpolate(max(at - 1.0, 0.0)), line.interpolate(min(at + 1.0, line.length))
    dx, dy = b.x - a.x, b.y - a.y
    size = math.hypot(dx, dy)
    if size == 0:
        return [nearest]
    nx, ny = -dy / size * radius_m, dx / size * radius_m
    across = LineString([(point.x - nx, point.y - ny), (point.x + nx, point.y + ny)])
    # The nearest link is always in, even when the site is just past its end.
    hit = chosen[chosen.line.intersects(across) | chosen.link_id.eq(nearest.link_id.iloc[0])]
    # Where along the line each link is crossed (or comes closest); within 1 m is one place.
    where = hit.line.map(lambda g: across.project(nearest_points(g, across)[0]))
    hit = hit.assign(where=where.to_numpy()).sort_values("where")
    place = (hit["where"].diff().fillna(np.inf) > 1.0).cumsum()
    return [group for _, group in hit.groupby(place.to_numpy(), sort=True)]


RANK = {
    "trunk": 6, "primary": 6, "secondary": 5, "tertiary": 4, "pedestrian": 4,
    "cycleway": 3, "path": 3, "unclassified": 2, "residential": 2, "living_street": 2,
    "footway": 2, "service": 1, "track": 1, "steps": 1,
}  # fmt: skip


def _main_link(chosen: pd.DataFrame) -> pd.Series:
    """The matched link of the highest road class: the street the counter is on."""
    rank = chosen.highway.astype(str).str.removesuffix("_link").map(RANK).fillna(0)
    return chosen.iloc[int(np.argmax(rank.to_numpy()))]


def _one_way(used: pd.DataFrame, mode: str) -> bool:
    """Whether every link used is one way for the mode (walkers never are)."""
    if mode == "walk":
        return False
    one_way = used.oneway.isin(["yes", "-1"]) & used.oneway_bicycle.ne("no")
    bike_rule = used.oneway_bicycle.isin(["yes", "-1"])
    return bool((one_way | bike_rule).all())


def _label(key: str) -> str:
    """A street key from :func:`street_names` as a plain name ("" for an unnamed link)."""
    return "" if "\x00" in key or key.startswith("unnamed ") else key
