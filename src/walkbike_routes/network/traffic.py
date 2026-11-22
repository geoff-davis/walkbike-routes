"""Daily traffic counts laid onto the street network.

Counts come as lines (HPMS road sections) drawn independently of
OpenStreetMap.  A street segment takes the count of the nearest counted line
within ``MAX_DISTANCE_M`` of its midpoint that runs the same way, within
``MAX_ANGLE_DEG``.  OpenStreetMap draws each side of a divided road as its
own one-way line, often 15-25 m from the counted centre line, so one-way
streets reach ``MAX_DIVIDED_DISTANCE_M``; the direction test stops a cross street from taking the
count of the road it meets.  Both directions are measured within 10 m of
the matched point, so a bend elsewhere in either line does not matter.
Only streets cars may use are matched, so a cycleway or footway beside an
arterial keeps no count.  Streets with no
match keep a missing count, and presets fall back to road class there.
"""

from __future__ import annotations

import math

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry.base import BaseGeometry

MAX_DISTANCE_M = 15.0
MAX_DIVIDED_DISTANCE_M = 30.0  # one carriageway of a divided road, drawn apart from its centre
MAX_ANGLE_DEG = 20.0
MIN_LENGTH_M = 1.0
COUNTED_HIGHWAYS = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
    "living_street", "motorway_link", "trunk_link", "primary_link", "secondary_link",
    "tertiary_link",
}  # fmt: skip


def _bearing(line: BaseGeometry, at: float, half: float) -> float:
    """The undirected bearing in degrees of a line around a distance along it."""
    start = line.interpolate(max(at - half, 0.0))
    end = line.interpolate(min(at + half, line.length))
    if start.equals(end):
        return math.nan
    return math.degrees(math.atan2(end.y - start.y, end.x - start.x)) % 180.0


def match_counts(links: gpd.GeoDataFrame, counts: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """The links with ``traffic_aadt`` and ``traffic_aadt_source`` columns.

    ``counts`` holds lines with ``aadt`` and ``source`` columns, in any
    coordinate system; ``links`` must be in a projected one, in metres.
    """
    out = links.copy()
    out["traffic_aadt"] = np.nan
    out["traffic_aadt_source"] = pd.Series(None, index=out.index, dtype=object)
    if out.empty or counts.empty:
        return out
    if out.crs is None or not out.crs.is_projected:
        raise ValueError("links must be in a projected coordinate system, in metres")
    lines = counts.to_crs(out.crs).explode(index_parts=False)  # a bearing needs one line
    lines = lines[(lines.aadt.astype(float) > 0) & lines.geom_type.eq("LineString")]
    lines = lines.reset_index(drop=True)
    eligible = out.highway.astype(str).isin(COUNTED_HIGHWAYS) & (out.length >= MIN_LENGTH_M)
    if "car_allowed" in out:
        eligible &= out.car_allowed.fillna(True).astype(bool)
    if lines.empty or not eligible.any():
        return out
    streets = out.loc[eligible, ["geometry"]].copy()
    oneway = out.get("oneway", pd.Series("no", index=out.index)).astype(str).isin({"yes", "-1"})
    streets["reach"] = np.where(oneway[eligible], MAX_DIVIDED_DISTANCE_M, MAX_DISTANCE_M)
    streets["bearing"] = [
        _bearing(g, g.length / 2, min(10.0, g.length / 2)) for g in streets.geometry
    ]
    streets["geometry"] = streets.geometry.interpolate(0.5, normalized=True)
    near = gpd.sjoin(
        streets,
        gpd.GeoDataFrame(geometry=lines.buffer(MAX_DIVIDED_DISTANCE_M), crs=out.crs),
        predicate="within",
    )
    best: dict[object, tuple[float, int]] = {}
    for index, row in near.iterrows():
        line = lines.geometry.iloc[row.index_right]
        distance = float(line.distance(row.geometry))
        if distance > row.reach:
            continue
        along = line.project(row.geometry)
        difference = abs(row.bearing - _bearing(line, along, 10.0))
        if not min(difference, 180.0 - difference) <= MAX_ANGLE_DEG:  # NaN fails too
            continue
        if index not in best or distance < best[index][0]:
            best[index] = (distance, int(row.index_right))
    for index, (_, line_index) in best.items():
        out.loc[index, "traffic_aadt"] = float(lines.aadt.iloc[line_index])
        out.loc[index, "traffic_aadt_source"] = str(lines.source.iloc[line_index])
    return out


def search_bbox(links: gpd.GeoDataFrame) -> tuple[float, float, float, float]:
    """The links' bounds in degrees, widened so a count just beyond them is found."""
    west, south, east, north = links.to_crs(4326).total_bounds
    pad = 0.001  # about 110 m north-south and 85 m east-west at 40 degrees north
    return (west - pad, south - pad, east + pad, north + pad)


def coverage(links: gpd.GeoDataFrame) -> pd.DataFrame:
    """Street length, and the share with a count, by road class."""
    motor = links[links.highway.astype(str).isin(COUNTED_HIGHWAYS)]
    length = motor.length
    counted = motor.traffic_aadt.notna() if "traffic_aadt" in motor else False
    table = pd.DataFrame({"highway": motor.highway.astype(str), "km": length / 1000})
    table["counted_km"] = np.where(counted, table.km, 0.0)
    summary = table.groupby("highway")[["km", "counted_km"]].sum()
    summary["share_counted"] = summary.counted_km / summary.km
    return summary
