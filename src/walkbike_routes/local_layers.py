"""Local layers: a city's or agency's own evidence, laid over OpenStreetMap.

Cities often keep better records than OpenStreetMap of sidewalks, traffic
signals, posted speeds and bikeways.  Each layer in a study's
``local_layers`` replaces one OSM attribute where it matches a street:

- ``sidewalks`` (lines): a street with a sidewalk line alongside it gets
  sidewalk evidence (``sidewalk = local``), which counts as a sidewalk even
  where OSM says there is none;
- ``signals`` (points): the nearest intersection gets a traffic signal;
- ``speeds`` (lines): the layer's speed in mph replaces OSM's ``maxspeed``;
- ``bikeways`` (lines): the layer's facility type, turned into ``none``,
  ``lane``, ``track`` or ``path`` by the config's ``values``, replaces OSM's.

A line feature matches a street when it lies within ``match_m`` of the
street's midpoint and runs within 30 degrees of the same direction, so a
cross street does not take the evidence of the street it meets.  Of several
matches the nearest wins.  Each changed attribute records where it came from
in a ``*_source`` column.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from walkbike_routes.config import LAYER_KINDS, ConfigError, LocalLayer

OSM = "OpenStreetMap"
MAXIMUM_ANGLE_DEG = 30.0
SOURCES = {"sidewalk": "sidewalk_source", "speed_mph": "speed_source",
           "bike_facility": "bike_facility_source"}  # fmt: skip
INTERSECTION_DEGREE = 3  # links meeting at a node for it to count as an intersection


def apply(
    links: gpd.GeoDataFrame, nodes: gpd.GeoDataFrame, layers: Sequence[LocalLayer]
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, dict[str, int]]:
    """The street and node tables with each layer applied, and the matches per layer."""
    links, nodes = links.copy(), nodes.copy()
    counts: dict[str, int] = {}
    for layer in layers:
        features = read(layer, links.crs)
        match_m = layer.match_m or LAYER_KINDS[layer.kind]  # 0: the kind's default
        label = layer.name or layer.file.name
        if layer.kind == "signals":
            hit = _signal_nodes(nodes, links, features, match_m)
            nodes["control_source"] = nodes.get("control_source", pd.Series(OSM, nodes.index))
            nodes.loc[hit, "control"] = "signal"
            nodes.loc[hit, "control_source"] = label
            counts[label] = int(hit.sum())
            continue
        rows = match_lines(links, features, match_m).dropna().astype(int)
        column, values = _values(layer, features, rows)
        source = SOURCES[column]
        if source not in links:
            links[source] = OSM
        links[column] = links[column].astype(object)
        links.loc[values.index, column] = values
        links.loc[values.index, source] = label
        counts[label] = len(values)
    return links, nodes, counts


def _values(
    layer: LocalLayer, features: gpd.GeoDataFrame, rows: pd.Series
) -> tuple[str, pd.Series]:
    """The street column a layer sets, and its value on each matched street."""
    positions = rows.to_numpy()
    if layer.kind == "sidewalks":
        return "sidewalk", pd.Series("local", index=rows.index, dtype=object)
    assert layer.column is not None  # the config requires it for speeds and bikeways
    if layer.kind == "speeds":
        numbers = pd.to_numeric(features[layer.column], errors="coerce")
        speeds = pd.Series(numbers, dtype="Float64").to_numpy(dtype=float, na_value=np.nan)
        picked = pd.Series(speeds[positions], index=rows.index, dtype=float)
        return "speed_mph", picked[picked > 0]  # 0 or blank means no posted speed
    facilities = _facilities(features, layer).to_numpy(dtype=object)
    return "bike_facility", pd.Series(facilities[positions], index=rows.index, dtype=object)


def read(layer: LocalLayer, crs: Any) -> gpd.GeoDataFrame:
    """The layer's features in ``crs``, after its ``where`` filter."""
    if not layer.file.exists():
        raise ConfigError(f"local_layers: no such file {layer.file}")
    try:
        features = (
            gpd.read_parquet(layer.file)
            if layer.file.suffix == ".parquet"
            else gpd.read_file(layer.file)
        )
    except (OSError, ValueError, RuntimeError) as err:  # pyogrio raises RuntimeErrors
        raise ConfigError(f"local_layers: could not read {layer.file}: {err}") from err
    if features.crs is None:
        raise ConfigError(f"local_layers: {layer.file} has no coordinate system")
    needed = [c for c in [layer.column, *layer.where] if c is not None]
    missing = [c for c in needed if c not in features.columns]
    if missing:
        raise ConfigError(f"local_layers: {layer.file} has no column {missing[0]!r}")
    if features.geometry.name != "geometry":
        features = features.rename_geometry("geometry")
    for column, allowed in layer.where.items():
        features = features[_allowed(features[column], allowed)]
    features = features[features.geometry.notna() & ~features.geometry.is_empty]
    # One row per part, so each part of a multi-line or multi-point is matched on its own.
    features = features.explode(index_parts=False)
    expected = {"signals": ("Point", "MultiPoint")}.get(
        layer.kind, ("LineString", "MultiLineString")
    )
    wrong = sorted(set(features.geom_type) - set(expected))
    if wrong:
        raise ConfigError(
            f"local_layers: {layer.kind} in {layer.file} must be {expected[0]}s, not {wrong[0]}s"
        )
    projected: gpd.GeoDataFrame = features.to_crs(crs)
    return projected.reset_index(drop=True)


def _allowed(column: pd.Series, allowed: tuple[str, ...]) -> pd.Series:
    """Rows whose value is one of ``allowed``, compared as text and, if numeric, as numbers."""
    keep = column.astype(str).str.strip().isin(allowed)
    numbers = pd.to_numeric(pd.Series(allowed), errors="coerce").dropna()
    if not numbers.empty:
        keep |= pd.to_numeric(column, errors="coerce").isin(numbers.to_numpy())
    return keep


def match_lines(links: gpd.GeoDataFrame, features: gpd.GeoDataFrame, match_m: float) -> pd.Series:
    """For each street, the row of the nearest feature running alongside it, or NaN."""
    result = pd.Series(np.nan, index=links.index)
    if features.empty or links.empty:
        return result
    middles = gpd.GeoDataFrame(
        {"link": links.index}, geometry=links.geometry.interpolate(0.5, normalized=True).to_numpy(),
        crs=links.crs,
    )  # fmt: skip
    pairs = gpd.sjoin(middles, features[["geometry"]], predicate="dwithin", distance=match_m)
    if pairs.empty:
        return result
    point = pairs.geometry.to_numpy()
    lines = features.geometry.to_numpy()[pairs.index_right.to_numpy()]
    distance = shapely.distance(point, lines)
    street = _direction(links.geometry.to_numpy()[_positions(links, pairs.link)], point)
    feature = _direction(lines, point)
    difference = np.abs((street - feature + 90.0) % 180.0 - 90.0)  # undirected, 0 to 90
    pairs = pairs.assign(distance=distance, parallel=difference <= MAXIMUM_ANGLE_DEG)
    pairs = pairs[pairs.parallel].sort_values(["link", "distance", "index_right"])
    best = pairs.drop_duplicates("link")
    result.loc[best.link.to_numpy()] = best.index_right.to_numpy(dtype=float)
    return result


def _positions(frame: gpd.GeoDataFrame, labels: pd.Series) -> np.ndarray:
    return np.asarray(frame.index.get_indexer(pd.Index(labels)))


def _direction(lines: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Each line's direction, in degrees, over the 2 m around its point nearest ``points``.

    Measured locally so that a bent street is compared where it meets the
    feature, not by its end-to-end bearing.
    """
    along = shapely.line_locate_point(lines, points)
    length = shapely.length(lines)
    ahead = shapely.line_interpolate_point(lines, np.minimum(along + 1.0, length))
    behind = shapely.line_interpolate_point(lines, np.maximum(along - 1.0, 0.0))
    dx = shapely.get_x(ahead) - shapely.get_x(behind)
    dy = shapely.get_y(ahead) - shapely.get_y(behind)
    return np.asarray(np.degrees(np.arctan2(dy, dx)))


def _signal_nodes(
    nodes: gpd.GeoDataFrame,
    links: gpd.GeoDataFrame,
    signals: gpd.GeoDataFrame,
    match_m: float,
) -> pd.Series:
    """Intersections that have a signal within ``match_m``: the nearest to each signal."""
    degree = pd.concat([links.u.astype(str), links.v.astype(str)]).value_counts()
    junctions = nodes[nodes.node_id.astype(str).map(degree).fillna(0).ge(INTERSECTION_DEGREE)]
    hit = pd.Series(False, index=nodes.index)
    if signals.empty or junctions.empty:
        return hit
    joined = gpd.sjoin_nearest(
        signals[["geometry"]], junctions[["geometry"]], max_distance=match_m, how="inner"
    )
    hit.loc[joined.index_right.unique()] = True
    return hit


def _facilities(features: gpd.GeoDataFrame, layer: LocalLayer) -> pd.Series:
    assert layer.column is not None
    text = features[layer.column].astype(str).str.strip()
    unknown = sorted(set(text) - set(layer.values))
    if unknown:
        raise ConfigError(
            f"local_layers: {layer.file} has {layer.column} {unknown[0]!r}, "
            "which values does not map; map it to none, lane, track, path or boulevard"
        )
    return text.map(layer.values)


def describe(counts: dict[str, int]) -> str:
    """One line for the log."""
    return ", ".join(f"{name}: {n} matched" for name, n in counts.items()) if counts else "none"


__all__ = ["apply", "describe", "match_lines", "read"]
