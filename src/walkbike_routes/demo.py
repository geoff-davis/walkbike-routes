"""A made-up town for tests and the demo: streets, homes, a school and a downtown.

Nothing here describes a real place.  The town is a 13 by 13 grid of
intersections 120 m apart, set on open ground in UTM zone 13N so that it has
real coordinates for maps.  It has two crossing arterials with signals where
they meet the collectors, a painted bike lane on one collector, a walking and
cycling path along its west edge, a corner with no recorded sidewalks, an
arterial stretch with none, and a hill in the north-west.  Homes and their
residents are drawn from a seeded generator, so the same seed gives the same
town.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point, box

CRS = "EPSG:32613"
SIZE = 13  # intersections along each side
SPACING_M = 120.0
ORIGIN = (500_000.0, 4_400_000.0)  # south-west corner, in CRS metres
ARTERIAL = 6  # row and column index of the two arterials
COLLECTORS = (3, 9)
PATH_COLUMN = 0
BIKE_LANE_ROW = 3
SCHOOL_BLOCK = (9, 9)  # (row, column) of the block's south-west corner
DOWNTOWN = (5, 7)  # first and last row and column of the downtown square
HILL = (10, 2)  # node (row, column) at the top of the hill
NO_SIDEWALK_FROM = 10  # residential streets north-east of here have none recorded
SEED = 7

ROW_NAMES = [f"{chr(ord('A') + r)} Street" for r in range(SIZE)]
COLUMN_NAMES = [
    f"{c + 1}{'st' if c == 0 else 'nd' if c == 1 else 'rd' if c == 2 else 'th'} Avenue"
    for c in range(SIZE)
]
ROW_NAMES[ARTERIAL] = "Main Street"
COLUMN_NAMES[ARTERIAL] = "Central Avenue"
COLUMN_NAMES[PATH_COLUMN] = "West Edge Path"


@dataclass(frozen=True)
class Town:
    """The made-up town's layers, all in :data:`CRS`."""

    nodes: gpd.GeoDataFrame
    links: gpd.GeoDataFrame
    homes: gpd.GeoDataFrame
    destinations: gpd.GeoDataFrame
    access_points: gpd.GeoDataFrame
    # Jobs by block (block_id, jobs, a point each), for network flows; None when not needed.
    jobs: gpd.GeoDataFrame | None = None
    # Where a real area's data came from (extract, releases, years), for the manifest.
    sources: dict[str, Any] = field(default_factory=dict)


def node_id(row: int, column: int) -> str:
    return f"n{row:02d}_{column:02d}"


def xy(row: int, column: int) -> tuple[float, float]:
    return ORIGIN[0] + column * SPACING_M, ORIGIN[1] + row * SPACING_M


def build(seed: int = SEED) -> Town:
    """The town; ``seed`` sets the homes and their residents."""
    links = _links()
    nodes = _nodes()
    homes = _homes(np.random.default_rng(seed))
    destinations, access_points = _destinations()
    return Town(nodes, links, homes, destinations, access_points)


def _street_class(row: int | None, column: int | None) -> str:
    """The road class of a street running along a row (or a column)."""
    line = row if row is not None else column
    if column == PATH_COLUMN:
        return "path"
    if line == ARTERIAL:
        return "primary"
    if line in COLLECTORS:
        return "tertiary"
    return "residential"


def _links() -> gpd.GeoDataFrame:
    rows: list[dict[str, object]] = []
    for r in range(SIZE):
        for c in range(SIZE):
            for dr, dc in ((0, 1), (1, 0)):
                r2, c2 = r + dr, c + dc
                if r2 >= SIZE or c2 >= SIZE:
                    continue
                along_row = dr == 0
                highway = _street_class(r if along_row else None, None if along_row else c)
                name = ROW_NAMES[r] if along_row else COLUMN_NAMES[c]
                rows.append(
                    {
                        "link_id": f"{node_id(r, c)}-{node_id(r2, c2)}",
                        "u": node_id(r, c),
                        "v": node_id(r2, c2),
                        "name": name,
                        "highway": highway,
                        **_cross_section(highway, r, c, along_row),
                        "geometry": LineString([xy(r, c), xy(r2, c2)]),
                    }
                )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=CRS)


def _cross_section(highway: str, row: int, column: int, along_row: bool) -> dict[str, object]:
    lanes = {"primary": 4, "tertiary": 2, "residential": 2, "path": 0}[highway]
    speed = {"primary": 35, "tertiary": 25, "residential": 25, "path": None}[highway]
    bike = "path" if highway == "path" else "none"
    if along_row and row == BIKE_LANE_ROW and highway == "tertiary":
        bike = "lane"
    sidewalk = "both"
    if highway == "path":
        sidewalk = "path"
    elif highway == "residential" and row >= NO_SIDEWALK_FROM and column >= NO_SIDEWALK_FROM:
        sidewalk = "unknown"
    elif highway == "primary" and along_row and column >= NO_SIDEWALK_FROM:
        sidewalk = "none"
    return {"lanes": lanes, "speed_mph": speed, "bike_facility": bike, "sidewalk": sidewalk}


def _control(row: int, column: int) -> str:
    """Signals where an arterial meets an arterial or collector; stops where a
    collector or arterial meets a smaller street, or two collectors meet.  The
    path crosses streets with no control."""
    rank = {"path": 0, "residential": 1, "tertiary": 2, "primary": 3}
    major, minor = sorted(
        (rank[_street_class(row, None)], rank[_street_class(None, column)]), reverse=True
    )
    if major == 3 and minor >= 2:
        return "signal"
    if major >= 2 and minor >= 1:
        return "stop"
    return "none"


def _nodes() -> gpd.GeoDataFrame:
    hill = np.array(xy(*HILL))
    rows: list[dict[str, object]] = []
    for r in range(SIZE):
        for c in range(SIZE):
            distance = float(np.hypot(*(np.array(xy(r, c)) - hill)))
            rows.append(
                {
                    "node_id": node_id(r, c),
                    "control": _control(r, c),
                    "elevation_m": round(20 + 25 * np.exp(-(distance**2) / (2 * 300**2)), 2),
                    "geometry": Point(xy(r, c)),
                }
            )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=CRS)


def _skipped_block(row: int, column: int) -> bool:
    downtown = DOWNTOWN[0] <= row < DOWNTOWN[1] and DOWNTOWN[0] <= column < DOWNTOWN[1]
    return downtown or (row, column) == SCHOOL_BLOCK


def _homes(rng: np.random.Generator) -> gpd.GeoDataFrame:
    rows: list[dict[str, object]] = []
    for r in range(SIZE - 1):
        for c in range(SIZE - 1):
            if _skipped_block(r, c):
                continue
            x, y = xy(r, c)
            for k, (fx, fy) in enumerate(((0.3, 0.35), (0.7, 0.65))):
                rows.append(
                    {
                        "home_id": f"h{r:02d}_{c:02d}_{k}",
                        "residents_5_11": int(rng.poisson(3)),
                        "residents": int(rng.integers(20, 60)),
                        "households": int(rng.integers(8, 25)),
                        "geometry": Point(x + fx * SPACING_M, y + fy * SPACING_M),
                    }
                )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=CRS)


def jobs() -> gpd.GeoDataFrame:
    """Made-up jobs at block centres: many downtown, some at the school and along Main Street."""
    rows: list[dict[str, object]] = []
    for r in range(SIZE - 1):
        for c in range(SIZE - 1):
            downtown = DOWNTOWN[0] <= r < DOWNTOWN[1] and DOWNTOWN[0] <= c < DOWNTOWN[1]
            count = 400 if downtown else 60 if (r, c) == SCHOOL_BLOCK else 0
            if not count and r in (ARTERIAL - 1, ARTERIAL):  # shops either side of Main Street
                count = 20 + 5 * (c % 3)
            if count:
                x, y = xy(r, c)
                rows.append(
                    {
                        "block_id": f"demo{r:02d}{c:02d}",
                        "jobs": count,
                        "geometry": Point(x + SPACING_M / 2, y + SPACING_M / 2),
                    }
                )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=CRS)


def _destinations() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    sr, sc = SCHOOL_BLOCK
    x, y = xy(sr, sc)
    school = Point(x + SPACING_M / 2, y + SPACING_M / 2)
    low, high = xy(DOWNTOWN[0], DOWNTOWN[0]), xy(DOWNTOWN[1], DOWNTOWN[1])
    destinations = gpd.GeoDataFrame(
        {
            "destination_id": ["school", "downtown"],
            "name": ["Demo Elementary", "Downtown"],
            "kind": ["point", "area"],
        },
        geometry=[school, box(*low, *high)],
        crs=CRS,
    )
    gates = pd.DataFrame(
        {
            "destination_id": ["school", "school"],
            "access_point_id": ["school_south_gate", "school_east_gate"],
        }
    )
    access = gpd.GeoDataFrame(
        gates,
        geometry=[Point(x + SPACING_M / 2, y + 8), Point(x + SPACING_M - 8, y + SPACING_M / 2)],
        crs=CRS,
    )
    return destinations, access
