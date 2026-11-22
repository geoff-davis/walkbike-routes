"""Ground elevation at network nodes from the USGS 3D Elevation Program (3DEP).

The 3DEP image service samples its best available model (1 m lidar where it
exists, 10 m elsewhere) at a list of points, so no rasters are downloaded.
"""

from __future__ import annotations

import json
import math

import geopandas as gpd

from walkbike_routes.download.http import DownloadError, Fetcher

SERVICE = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer"
BATCH = 400
NO_DATA = -1e30  # the service's fill value lies far below any real ground


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
    heights: list[float] = []
    for start in range(0, len(coordinates), BATCH):
        batch = coordinates[start : start + BATCH]
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
        for index in range(len(batch)):
            heights.append(_height(found.get(index)))
    result = nodes.copy()
    result["elevation_m"] = heights
    if result.elevation_m.isna().all() and len(result):
        raise DownloadError("3DEP returned no elevations for any node")
    return result


def _height(value: object) -> float:
    try:
        height = float(str(value))
    except ValueError:
        return math.nan
    return height if math.isfinite(height) and height > NO_DATA / 2 else math.nan
