"""Recorded bicycle trips (GPS traces) in one format, for checking route choice.

A traces file has one row per trip, as GeoParquet, GeoJSON or any file
GeoPandas reads, with line geometry (the route ridden, start to end) and
these columns:

- ``trip_id``: unique text;
- ``user_id``: who rode it, as an anonymous code (scores are resampled by
  rider, since a few riders often make most of the trips);
- ``purpose``: the trip's purpose as recorded (may be blank);
- ``group``: any rider grouping to break results down by (may be blank).

Traces are records of real people's trips: keep them out of the repository,
and publish only figures computed from them, with the source's credit.

:func:`eligible` decides which trips are compared.  It looks only at each
trace, never at a modelled route, so it favours no preset:

- **outside**: not wholly inside the comparison area;
- **too short** or **too long** for a trip with a destination;
- **leisure ride**: recorded as exercise, where the route is the point;
- **loop**: ends close together compared with the distance ridden;
- **broken**: pieces that do not join up within :data:`MAX_GAP_M`.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, MultiLineString, Point
from shapely.geometry.base import BaseGeometry

from walkbike_routes.exceptions import AccessibilityError

COLUMNS = ("trip_id", "user_id", "purpose", "group")
USED = "used"
OUTSIDE = "outside the comparison area"
TOO_SHORT = "too short"
TOO_LONG = "too long"
LEISURE = "leisure ride"
LOOP = "loop"
BROKEN = "broken trace"
MIN_M = 500.0
MAX_M = 15_000.0
LOOP_RATIO = 0.4  # ends at least this share of the distance ridden apart
MIN_STRAIGHT_M = 300.0
MAX_GAP_M = 50.0
LEISURE_PURPOSES = ("exercise",)


class TracesError(AccessibilityError):
    """A traces file that cannot be used."""


def read(path: Path) -> gpd.GeoDataFrame:
    """Read and check a traces file."""
    try:
        parquet = path.suffix.lower() in {".parquet", ".geoparquet"}
        frame = gpd.read_parquet(path) if parquet else gpd.read_file(path)
    except (OSError, ValueError) as error:
        raise TracesError(f"{path}: not a readable traces file ({error})") from error
    return check(frame)


def check(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """The traces with their columns checked and tidied; raises :class:`TracesError`."""
    missing = [c for c in COLUMNS if c not in frame.columns]
    if missing:
        raise TracesError(f"traces: missing columns {missing}")
    if frame.crs is None:
        raise TracesError("traces: no coordinate system")
    kinds = set(frame.geometry.geom_type.dropna())
    if frame.geometry.isna().any() or kinds - {"LineString", "MultiLineString"}:
        raise TracesError("traces: every trip must be a line")
    out = frame[[*COLUMNS, "geometry"]].copy()
    for name in COLUMNS:
        out[name] = out[name].fillna("").astype(str).str.strip()
    if out.trip_id.eq("").any() or out.trip_id.duplicated().any():
        raise TracesError("traces: trip_id must be filled in and unique")
    if out.user_id.eq("").any():
        raise TracesError("traces: user_id must be filled in")
    return gpd.GeoDataFrame(out.reset_index(drop=True), geometry="geometry", crs=frame.crs)


def joined(geometry: BaseGeometry, max_gap_m: float = MAX_GAP_M) -> LineString | None:
    """The trace as one line, start to end, or None if its pieces do not join up.

    Pieces are taken in their recorded order; each next piece is joined at
    whichever of its ends is within ``max_gap_m`` of the line so far, so a
    trace whose pieces are out of order counts as broken.
    """
    if isinstance(geometry, LineString):
        return geometry if geometry.length > 0 else None
    if not isinstance(geometry, MultiLineString):
        return None
    coords = list(geometry.geoms[0].coords)
    for piece in list(geometry.geoms)[1:]:
        points = list(piece.coords)
        end = Point(coords[-1])
        if Point(points[0]).distance(end) <= max_gap_m:
            coords += points
        elif Point(points[-1]).distance(end) <= max_gap_m:
            coords += points[::-1]
        else:
            return None
    return LineString(coords)


def eligible(
    traces: gpd.GeoDataFrame, within: BaseGeometry | None = None
) -> tuple[pd.Series, gpd.GeoSeries]:
    """Each trip's status (:data:`USED` or why not) and its trace as one line.

    ``traces`` must be in a metric CRS (lengths are in metres); ``within`` is
    the comparison area in that CRS.
    """
    statuses = []
    lines: list[LineString | None] = []
    for geometry, purpose in zip(traces.geometry, traces.purpose, strict=True):
        line = joined(geometry)
        lines.append(line)
        statuses.append(_status(geometry, str(purpose), line, within))
    return (
        pd.Series(statuses, index=traces.index, name="status"),
        gpd.GeoSeries(lines, index=traces.index, crs=traces.crs),  # type: ignore[arg-type]
    )


def _status(
    geometry: BaseGeometry, purpose: str, line: LineString | None, within: BaseGeometry | None
) -> str:
    # The joined line, gaps and all, is what is routed and scored.
    if within is not None and not (line or geometry).within(within):
        return OUTSIDE
    if purpose.lower() in LEISURE_PURPOSES:
        return LEISURE
    if line is None:
        return BROKEN
    if line.length < MIN_M:
        return TOO_SHORT
    if line.length > MAX_M:
        return TOO_LONG
    straight = Point(line.coords[0]).distance(Point(line.coords[-1]))
    if straight < MIN_STRAIGHT_M or straight < LOOP_RATIO * line.length:
        return LOOP
    return USED
