"""Ground elevation at network nodes from the USGS 3D Elevation Program (3DEP).

The 3DEP image service samples its best available model (1 m lidar where it
exists, 10 m elsewhere) at a list of points, so no rasters are downloaded.

Each point's answer is kept in one table in the cache folder
(:data:`POINTS_FILE`), keyed by its rounded coordinates, so a rerun asks only
for points it has not seen.  A changed network (new map data, an override, a
moved node) then costs a request for the changed points, not for every batch
after the first change.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import geopandas as gpd

from walkbike_routes.download.http import DownloadError, Fetcher

SERVICE = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer"
BATCH = 400
NO_DATA = -1e30  # the service's fill value lies far below any real ground
POINTS_FILE = "3dep-points.json"


def _answer_problem(answer: object) -> str | None:
    if not isinstance(answer, dict):
        return "not a JSON object"
    if "error" in answer:
        return f"3DEP refused the request: {answer['error']}"
    if not isinstance(answer.get("samples"), list):
        return "the answer has no samples"
    return None


def node_elevations(nodes: gpd.GeoDataFrame, fetcher: Fetcher) -> gpd.GeoDataFrame:
    """The nodes with an ``elevation_m`` column (missing where 3DEP has no data)."""
    points = nodes.to_crs(4326)
    coordinates = [
        (round(x, 7), round(y, 7))
        for x, y in zip(points.geometry.x, points.geometry.y, strict=True)
    ]
    table_path = fetcher.cache / POINTS_FILE
    known = _read_points(table_path)
    wanted = list(dict.fromkeys(c for c in coordinates if _key(c) not in known))
    for start in range(0, len(wanted), BATCH):
        batch = wanted[start : start + BATCH]
        answer = fetcher.json(
            f"{SERVICE}/getSamples",
            data={
                "geometry": json.dumps(
                    {"points": [list(c) for c in batch], "spatialReference": {"wkid": 4326}}
                ),
                "geometryType": "esriGeometryMultipoint",
                "returnFirstValueOnly": "true",
                "interpolation": "RSP_BilinearInterpolation",
                "f": "json",
            },
            name="3dep",
            check=_answer_problem,
        )
        found = {int(s["locationId"]): s.get("value") for s in answer["samples"]}
        for index, coordinate in enumerate(batch):
            height = _height(found.get(index))
            known[_key(coordinate)] = None if math.isnan(height) else height
        _write_points(table_path, known)  # after each batch, so a stopped run resumes
    result = nodes.copy()
    result["elevation_m"] = [_stored(known[_key(c)]) for c in coordinates]
    if result.elevation_m.isna().all() and len(result):
        raise DownloadError("3DEP returned no elevations for any node")
    return result


def _key(coordinate: tuple[float, float]) -> str:
    return f"{coordinate[0]:.7f},{coordinate[1]:.7f}"


def _stored(value: float | None) -> float:
    return math.nan if value is None else value


def _read_points(path: Path) -> dict[str, float | None]:
    """The saved point table, or an empty one when there is none or it is damaged."""
    try:
        saved = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(saved, dict):
        return {}
    return {str(k): (float(v) if isinstance(v, int | float) else None) for k, v in saved.items()}


def _write_points(path: Path, known: dict[str, float | None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    part.write_text(json.dumps(known))
    part.replace(path)


def _height(value: object) -> float:
    try:
        height = float(str(value))
    except ValueError:
        return math.nan
    return height if math.isfinite(height) and height > NO_DATA / 2 else math.nan
