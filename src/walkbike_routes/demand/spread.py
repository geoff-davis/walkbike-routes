"""Spread Census block counts over the homes in each block.

A block's residents (or households, or an age group) are shared among the
buildings in it that could be homes, in proportion to floor area: footprint
area times the number of floors (from ``num_floors``, else ``height`` at 3 m a
floor, else one).  A building tagged with a type counts only if the type is a
home (a house or apartments, not a garage, shop, school or hospital); an
untagged footprint counts if it is at least ``minimum_area_m2``.  A
block with no such building keeps one point carrying all its counts.

This is dasymetric mapping with buildings as the ancillary layer.  It places
people where homes are rather than at the middle of a block, which matters
for large blocks with homes along one edge; it does not know how many people
live in any one building.
"""

from __future__ import annotations

import math

import geopandas as gpd
import numpy as np
import pandas as pd

# Overture building classes that are homes; any other class rules a building out.
HOME_CLASSES = {
    "apartments", "bungalow", "cabin", "detached", "dormitory", "dwelling_house", "farm",
    "ger", "house", "houseboat", "residential", "semi", "semidetached_house",
    "static_caravan", "stilt_house", "terrace", "trullo",
}  # fmt: skip
METRES_PER_FLOOR = 3.0
MAXIMUM_FLOORS = 60.0


def home_buildings(buildings: gpd.GeoDataFrame, *, minimum_area_m2: float) -> gpd.GeoDataFrame:
    """The buildings that could be homes, with their ``floor_area_m2``.

    ``buildings`` must be in a projected CRS in metres.
    """
    blank = pd.Series(None, index=buildings.index, dtype=object)
    subtype = buildings.get("subtype", blank).fillna("").astype(str).str.lower()
    kind = buildings.get("class", blank).fillna("").astype(str).str.lower()
    area = buildings.geometry.area
    # The class is the finer tag, so it decides when present; else the subtype.
    by_class = kind.ne("")
    by_subtype = ~by_class & subtype.ne("")
    untagged = ~by_class & ~by_subtype
    keep = (
        (by_class & kind.isin(HOME_CLASSES))
        | (by_subtype & subtype.eq("residential"))
        | (untagged & area.ge(minimum_area_m2))
    )
    result = buildings[keep].copy()
    nothing = pd.Series(np.nan, index=result.index)
    floors = pd.to_numeric(result.get("num_floors", nothing), errors="coerce")
    height = pd.to_numeric(result.get("height", nothing), errors="coerce")
    floors = floors.where(floors.gt(0), np.floor(height / METRES_PER_FLOOR))
    floors = floors.where(floors.ge(1), 1.0).clip(upper=MAXIMUM_FLOORS)
    result["floor_area_m2"] = area[keep] * floors
    return result


def spread_blocks(
    blocks: gpd.GeoDataFrame,
    buildings: gpd.GeoDataFrame,
    *,
    counts: list[str],
    minimum_area_m2: float = 40.0,
) -> gpd.GeoDataFrame:
    """One home per building (or per block without one), sharing the block counts.

    ``blocks`` needs ``block_id``, the ``counts`` columns and block polygons;
    both layers must be in the same projected CRS.  Each home gets
    ``home_id``, ``block_id``, ``placed_by`` ("building" or "block"), the
    shared counts and a point geometry.  Counts add back to the block totals.
    """
    if blocks.crs != buildings.crs:
        raise ValueError("blocks and buildings must share a CRS")
    missing = [c for c in [*counts, "block_id"] if c not in blocks.columns]
    if missing:
        raise ValueError(f"blocks are missing {missing}")
    for column in counts:
        if not np.isfinite(blocks[column].to_numpy(float)).all():
            raise ValueError(f"blocks have a missing or non-finite {column!r} count")
    homes = home_buildings(buildings, minimum_area_m2=minimum_area_m2)
    points = gpd.GeoDataFrame(
        {"floor_area_m2": homes.floor_area_m2.to_numpy()},
        geometry=homes.geometry.representative_point().to_numpy(),
        crs=blocks.crs,
    )
    # "intersects", not "within": a point on the edge two blocks share is in neither.
    joined = gpd.sjoin(points, blocks[["block_id", "geometry"]], predicate="intersects")
    joined = joined.sort_values("block_id", kind="stable")
    joined = joined[~joined.index.duplicated(keep="first")].sort_index()
    totals = joined.groupby("block_id").floor_area_m2.transform("sum")
    joined = joined[totals.gt(0)]
    share = joined.floor_area_m2 / totals[totals.gt(0)]
    by_block = blocks.set_index("block_id")
    rows: dict[str, object] = {
        "block_id": joined.block_id.to_numpy(),
        "placed_by": "building",
    }
    for column in counts:
        rows[column] = by_block[column].reindex(joined.block_id).to_numpy(float) * share.to_numpy()
    placed = gpd.GeoDataFrame(rows, geometry=joined.geometry.to_numpy(), crs=blocks.crs)
    placed["home_id"] = [
        f"b{block}-{index}"
        for block, index in zip(placed.block_id, placed.groupby("block_id").cumcount(), strict=True)
    ]
    empty = blocks[~blocks.block_id.isin(set(placed.block_id))]
    alone = gpd.GeoDataFrame(
        {"block_id": empty.block_id.to_numpy(), "placed_by": "block",
         **{c: empty[c].to_numpy(float) for c in counts}},
        geometry=empty.geometry.representative_point().to_numpy(),
        crs=blocks.crs,
    )  # fmt: skip
    alone["home_id"] = "b" + alone.block_id.astype(str)
    result = pd.concat([placed, alone], ignore_index=True)
    columns = ["home_id", "block_id", "placed_by", *counts, "geometry"]
    out = gpd.GeoDataFrame(result[columns], geometry="geometry", crs=blocks.crs)
    for column in counts:
        expected = float(blocks[column].sum())
        if not math.isclose(float(out[column].sum()), expected, rel_tol=1e-9, abs_tol=1e-6):
            raise ValueError(f"{column!r} does not add back to the block totals")
    return out
