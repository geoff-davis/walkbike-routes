"""OpenStreetMap extracts from Geofabrik, cut down to the study area.

Geofabrik publishes daily OpenStreetMap extracts for each US state (California
in two halves) for bulk download.  The smallest extract covering the study
area is downloaded once and kept; the routable ways inside the area are then
read with ``osmium`` and saved in the element format the street parser reads,
so later runs need neither the extract nor the network.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import osmium
from shapely.geometry import LineString, box, shape

from walkbike_routes.download.http import DownloadError, Fetcher
from walkbike_routes.network.osm_streets import HIGHWAYS

INDEX = "https://download.geofabrik.de/index-v1.json"
Box = tuple[float, float, float, float]


def choose_region(bbox: Box, index: dict[str, Any]) -> tuple[str, str]:
    """The id and download address of the smallest extract covering a box."""
    wanted = box(*bbox)
    best: tuple[float, str, str] | None = None
    for feature in index.get("features", []):
        properties = feature.get("properties", {})
        url = properties.get("urls", {}).get("pbf")
        if not url or not feature.get("geometry"):
            continue
        outline = shape(feature["geometry"])
        if outline.covers(wanted) and (best is None or outline.area < best[0]):
            best = (outline.area, str(properties["id"]), str(url))
    if best is None:
        raise DownloadError("no Geofabrik extract covers the study area")
    return best[1], best[2]


def extract(path: Path, bbox: Box) -> dict[str, Any]:
    """Routable ways touching a box, with their nodes, from an OSM file.

    Ways are kept whole if they cross the box, so streets running over its
    edge are not cut off, and a long way with no node inside is still kept.
    """
    west, south, east, north = bbox
    area = box(*bbox)
    points: dict[int, tuple[float, float]] = {}
    tagged: dict[int, dict[str, str]] = {}
    ways: list[dict[str, Any]] = []
    processor = (
        osmium.FileProcessor(str(path))
        .with_locations()
        .with_filter(osmium.filter.KeyFilter("highway"))
    )
    for item in processor:
        if isinstance(item, osmium.osm.Node):
            lon, lat = item.location.lon, item.location.lat
            if west <= lon <= east and south <= lat <= north:
                tagged[item.id] = dict(item.tags)
        elif isinstance(item, osmium.osm.Way):
            if item.tags.get("highway") not in HIGHWAYS:
                continue
            refs = [(n.ref, n.lon, n.lat) for n in item.nodes if n.location.valid()]
            if len(refs) < 2 or not _crosses(refs, bbox, area):
                continue
            for ref, lon, lat in refs:
                points[ref] = (lon, lat)
            ways.append({"type": "way", "id": item.id, "nodes": [r for r, _, _ in refs],
                         "tags": dict(item.tags)})  # fmt: skip
    nodes = [
        {"type": "node", "id": ref, "lon": lon, "lat": lat,
         **({"tags": tagged[ref]} if ref in tagged else {})}
        for ref, (lon, lat) in sorted(points.items())
    ]  # fmt: skip
    return {"elements": [*nodes, *ways]}


def _crosses(refs: list[tuple[int, float, float]], bbox: Box, area: Any) -> bool:
    west, south, east, north = bbox
    if any(west <= lon <= east and south <= lat <= north for _, lon, lat in refs):
        return True
    lons = [lon for _, lon, _ in refs]
    lats = [lat for _, _, lat in refs]
    if max(lons) < west or min(lons) > east or max(lats) < south or min(lats) > north:
        return False
    return bool(LineString([(lon, lat) for _, lon, lat in refs]).intersects(area))


def area_elements(bbox: Box, fetcher: Fetcher) -> dict[str, Any]:
    """The study area's elements, from the cache or a (cached) Geofabrik extract."""
    key = hashlib.sha256(json.dumps([float(v) for v in bbox]).encode()).hexdigest()[:16]
    saved = fetcher.cache / f"osm-area-{key}.json"
    if saved.exists():
        loaded: dict[str, Any] = json.loads(saved.read_text())
        source = loaded.get("source")
        # Streets saved before the extract's date was recorded: read it now if the extract is kept.
        if isinstance(source, dict) and "data_until" not in source:
            extract_file = fetcher.cache / str(source.get("file", ""))
            if extract_file.is_file():
                source["data_until"] = data_until(extract_file)
        return loaded
    index = fetcher.json(INDEX, name="geofabrik-index", check=_index_problem)
    region, url = choose_region(bbox, index)
    path = fetcher.download(url, url.rsplit("/", 1)[-1])
    elements = extract(path, bbox)
    elements["source"] = {
        "extract": region,
        "url": url,
        "file": path.name,
        "data_until": data_until(path),
    }
    part = saved.with_suffix(".part")
    part.write_text(json.dumps(elements))
    part.replace(saved)
    return elements


def data_until(path: Path) -> str | None:
    """The time an extract's data runs to, from its header; None when it does not say."""
    try:
        reader = osmium.io.Reader(str(path), osmium.osm.osm_entity_bits.NOTHING)
    except RuntimeError:
        return None
    try:
        header = reader.header()
        stamp = header.get("osmosis_replication_timestamp") or header.get("timestamp")
    finally:
        reader.close()
    return str(stamp) if stamp else None


def forget(cache: Path) -> list[Path]:
    """Remove the saved extracts, index and cut-down streets, so the next run downloads them."""
    if not cache.is_dir():
        return []
    found = [
        path
        for pattern in ("osm-area-*.json", "geofabrik-index-*.json", "*.osm.pbf")
        for path in cache.glob(pattern)
    ]
    for path in found:
        path.unlink()
    return found


def _index_problem(answer: Any) -> str | None:
    ok = isinstance(answer, dict) and isinstance(answer.get("features"), list)
    return None if ok else "the Geofabrik index has no regions"
