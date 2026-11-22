"""Which street a separately mapped sidewalk runs beside.

OpenStreetMap draws many sidewalks as their own footways
(``footway=sidewalk``), apart from the street's centre line.  A route-choice
model estimated on centre lines, such as Broach's walking model
(:mod:`walkbike_routes.network.broach_walk`), counts a walk along such a
sidewalk as a walk along the street.  Each sidewalk takes the road class and
name of the nearest street cars may use, other than a parking aisle,
driveway or drive-through, whose centre line lies within
``MAX_DISTANCE_M`` of the sidewalk's midpoint and runs the same way, within
``MAX_ANGLE_DEG``.  A sidewalk with no such street keeps neither.

The street's own line often carries no ``sidewalk`` tag even when its
sidewalks are drawn beside it.  ``streets_with_separate_sidewalks`` finds
the streets that are lined by such sidewalks for at least ``MIN_COVERAGE``
of their length, so they can be read as ``sidewalk=separate``.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry.base import BaseGeometry

from walkbike_routes.network.traffic import COUNTED_HIGHWAYS, MIN_LENGTH_M, _bearing

MAX_DISTANCE_M = 25.0  # a sidewalk is rarely farther than this from the centre line
MAX_ANGLE_DEG = 20.0
STREETS = COUNTED_HIGHWAYS | {"service", "road"}  # any street cars may use...
NOT_STREETS = {"parking_aisle", "driveway", "drive-through"}  # ...but these service ways
MIN_COVERAGE = 0.8  # share of a street's length a matched sidewalk must line
# Looser than MAX_ANGLE_DEG: short street pieces inside junctions often bend off the
# sidewalk's line, but a piece turning across it (near 90 degrees) is not lined by it.
MAX_LINING_ANGLE_DEG = 45.0


def match_sidewalks(edges: gpd.GeoDataFrame) -> pd.DataFrame:
    """``beside_road`` and ``beside_name`` for each edge, empty where none applies.

    ``edges`` must be in a projected coordinate system, in metres, with
    ``highway`` and ``footway`` columns.
    """
    out = pd.DataFrame({"beside_road": "", "beside_name": ""}, index=edges.index)
    matches = _matches(edges)
    if not matches:
        return out
    highway = edges.highway.astype(str).to_numpy()
    names = _names(edges)
    walks = list(matches)
    streets = [matches[w] for w in walks]
    out.iloc[walks, 0] = highway[streets]
    out.iloc[walks, 1] = names[streets]
    return out


def streets_with_separate_sidewalks(edges: gpd.GeoDataFrame) -> pd.Series:
    """True for each street lined, along ``MIN_COVERAGE`` of its length, by
    separately mapped sidewalks matched to it or to a street of the same name.

    A sidewalk is often drawn as one long way beside several pieces of its
    street, but is matched to only one of them, so pieces of a named street
    share their sidewalks.  ``edges`` must be as for ``match_sidewalks``.
    """
    lined = np.zeros(len(edges), dtype=bool)
    matches = _matches(edges)
    if matches:
        names = _names(edges)
        street = _streets(edges)
        geometry = list(edges.geometry)
        groups: dict[str, list[int]] = {}
        for walk, matched in matches.items():
            key = names[matched] or f"\0{matched}"  # an unnamed street keeps its own sidewalks
            groups.setdefault(key, []).append(walk)
        by_name: dict[str, list[int]] = {}
        for position, name in enumerate(names):
            if name and street[position]:
                by_name.setdefault(name, []).append(position)
        for key, walks in groups.items():
            targets = by_name.get(key, []) if not key.startswith("\0") else [int(key[1:])]
            for position in targets:
                lined[position] = _lined(geometry[position], [geometry[w] for w in walks])
    return pd.Series(lined, index=edges.index)


def _lined(line: BaseGeometry, walks: list[BaseGeometry]) -> bool:
    """Whether sidewalks running the same way as ``line`` line enough of it."""
    if line.length <= 0:
        return False
    middle = line.interpolate(0.5, normalized=True)
    heading = _bearing(line, line.length / 2, min(10.0, line.length / 2))
    alongside = []
    for walk in walks:
        difference = abs(heading - _bearing(walk, walk.project(middle), 10.0))
        if min(difference, 180.0 - difference) <= MAX_LINING_ANGLE_DEG:  # NaN fails too
            alongside.append(walk.buffer(MAX_DISTANCE_M))
    if not alongside:
        return False
    covered = line.intersection(gpd.GeoSeries(alongside).union_all()).length
    return bool(covered >= MIN_COVERAGE * line.length)


def _names(edges: gpd.GeoDataFrame) -> np.ndarray:
    if "name" not in edges:
        return np.full(len(edges), "", dtype=object)
    names = edges["name"].astype(object)
    return np.array(["" if pd.isna(n) else str(n) for n in names], dtype=object)


def _streets(edges: gpd.GeoDataFrame) -> np.ndarray:
    """Whether each edge is a street a sidewalk can run beside."""
    road = edges.highway.astype(str).isin(STREETS)
    if "service" in edges:
        road &= ~edges.service.fillna("").astype(str).isin(NOT_STREETS)
    if "car_allowed" in edges:
        road &= edges.car_allowed.fillna(True).astype(bool)
    return road.to_numpy(dtype=bool)


def _matches(edges: gpd.GeoDataFrame) -> dict[int, int]:
    """Each matched sidewalk's row position, to that of the street it runs beside."""
    if edges.empty or "footway" not in edges:
        return {}
    if edges.crs is None or not edges.crs.is_projected:
        raise ValueError("edges must be in a projected coordinate system, in metres")
    sidewalk = edges.footway.fillna("").astype(str).eq("sidewalk") & (edges.length >= MIN_LENGTH_M)
    road = _streets(edges)
    if not sidewalk.any() or not road.any():
        return {}
    road_rows = np.flatnonzero(road)
    roads = gpd.GeoSeries(list(edges.geometry.iloc[road_rows]), crs=edges.crs)
    walk_rows = np.flatnonzero(sidewalk.to_numpy(dtype=bool))
    walks = gpd.GeoDataFrame(geometry=edges.geometry.iloc[walk_rows].reset_index(drop=True),
                             crs=edges.crs)  # fmt: skip
    walks["bearing"] = [_bearing(g, g.length / 2, min(10.0, g.length / 2)) for g in walks.geometry]
    walks["geometry"] = walks.geometry.interpolate(0.5, normalized=True)
    near = gpd.sjoin(
        walks,
        gpd.GeoDataFrame(geometry=roads.buffer(MAX_DISTANCE_M), crs=edges.crs),
        predicate="within",
    )
    best: dict[int, tuple[float, int]] = {}
    for walk, right, bearing, point in zip(
        near.index.to_numpy(dtype=int),
        near.index_right.to_numpy(dtype=int),
        near.bearing.to_numpy(dtype=float),
        list(near.geometry),
        strict=True,
    ):
        line = roads.iloc[right]
        distance = float(line.distance(point))
        if distance > MAX_DISTANCE_M:
            continue
        difference = abs(bearing - _bearing(line, line.project(point), 10.0))
        if not min(difference, 180.0 - difference) <= MAX_ANGLE_DEG:  # NaN fails too
            continue
        position = int(walk_rows[walk])
        if position not in best or distance < best[position][0]:
            best[position] = (distance, int(road_rows[right]))
    return {walk: street for walk, (_, street) in best.items()}
