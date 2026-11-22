"""OpenStreetMap streets and paths for the study extent.

By default they come from a Geofabrik extract (:mod:`.geofabrik`).  A study
may instead name its own file in ``area.osm_file``: an ``.osm.pbf`` or
``.osm`` extract, or a saved Overpass ``.json`` answer, which keeps a past
study's network fixed.  The public Overpass servers are not used: they ask
automated tools to run their own instance instead.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from walkbike_routes.download import geofabrik
from walkbike_routes.download.http import DownloadError, Fetcher

Box = tuple[float, float, float, float]


def elements(bbox: Box, fetcher: Fetcher, osm_file: Path | None = None) -> dict[str, Any]:
    """The routable ways and their nodes, in the Overpass JSON element format."""
    if osm_file is None:
        return geofabrik.area_elements(bbox, fetcher)
    if not osm_file.exists():
        raise DownloadError(f"OpenStreetMap file {osm_file} does not exist")
    if osm_file.suffix == ".json":
        answer = json.loads(osm_file.read_text())
        if not isinstance(answer, dict) or not isinstance(answer.get("elements"), list):
            raise DownloadError(f"{osm_file} is not an Overpass JSON answer")
        return answer
    if osm_file.name.endswith((".osm.pbf", ".osm", ".pbf")):
        return geofabrik.extract(osm_file, bbox)
    raise DownloadError(f"{osm_file}: expected .osm.pbf, .osm or Overpass .json")
