"""Made-up answers from TIGERweb, Geofabrik, the Census API, 3DEP and FHWA HPMS.

The town is a small grid near (-100, 40): nothing here describes a real place.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import osmium
from tenacity import wait_none

from walkbike_routes.download.http import Fetcher

WEST, SOUTH = -100.0, 40.0
STEP = 0.002  # about 170 m north-south, 170 m east-west at this latitude


def square(west: float, south: float, size: float) -> dict[str, Any]:
    ring = [
        [west, south],
        [west + size, south],
        [west + size, south + size],
        [west, south + size],
        [west, south],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


def place_answer(name: str = "Gridville") -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"NAME": f"{name} city", "BASENAME": name, "STATE": "31"},
                "geometry": square(WEST, SOUTH, 4 * STEP),
            }
        ],
    }


def overpass_answer() -> dict[str, Any]:
    """A 5 by 5 grid of nodes; row 2 is a signalled primary, column 0 a path."""
    elements: list[dict[str, Any]] = []
    for r in range(5):
        for c in range(5):
            node: dict[str, Any] = {
                "type": "node",
                "id": 100 + r * 10 + c,
                "lat": SOUTH + r * STEP,
                "lon": WEST + c * STEP,
            }
            if (r, c) == (2, 2):
                node["tags"] = {"highway": "traffic_signals"}
            if (r, c) == (4, 2):
                node["tags"] = {"highway": "stop"}
            elements.append(node)
    for r in range(5):
        tags = {"highway": "residential", "name": f"Row {r}", "sidewalk": "both"}
        if r == 2:
            tags = {"highway": "primary", "name": "Main", "lanes": "4", "maxspeed": "35 mph"}
        elements.append(
            {
                "type": "way",
                "id": 1 + r,
                "nodes": [100 + r * 10 + c for c in range(5)],
                "tags": tags,
            }
        )
    for c in range(5):
        tags = {"highway": "residential", "name": f"Column {c}"}
        if c == 0:
            tags = {"highway": "cycleway", "name": "Edge Path"}
        elements.append(
            {
                "type": "way",
                "id": 11 + c,
                "nodes": [100 + r * 10 + c for r in range(5)],
                "tags": tags,
            }
        )
    return {"version": 0.6, "elements": elements}


def pbf_bytes(answer: dict[str, Any]) -> bytes:
    """An .osm.pbf file holding an Overpass-style answer's elements."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "grid.osm.pbf"
        writer = osmium.SimpleWriter(str(path))
        elements = answer["elements"]
        for e in sorted((e for e in elements if e["type"] == "node"), key=lambda e: e["id"]):
            writer.add_node(
                osmium.osm.mutable.Node(
                    id=e["id"], location=(e["lon"], e["lat"]), tags=e.get("tags", {}), version=1
                )
            )
        for e in sorted((e for e in elements if e["type"] == "way"), key=lambda e: e["id"]):
            writer.add_way(
                osmium.osm.mutable.Way(id=e["id"], nodes=e["nodes"], tags=e["tags"], version=1)
            )
        writer.close()
        return path.read_bytes()


PBF_URL = "https://download.geofabrik.de/north-america/us/gridland-latest.osm.pbf"


def geofabrik_index() -> dict[str, Any]:
    """A continent and a smaller state inside it; the state should be chosen."""
    return {
        "features": [
            {"properties": {"id": "continent", "urls": {"pbf": "https://x.test/big.osm.pbf"}},
             "geometry": square(WEST - 10, SOUTH - 10, 20)},
            {"properties": {"id": "us/gridland", "urls": {"pbf": PBF_URL}},
             "geometry": square(WEST - 1, SOUTH - 1, 2)},
            {"properties": {"id": "elsewhere", "urls": {"pbf": "https://x.test/e.osm.pbf"}},
             "geometry": square(WEST + 5, SOUTH + 5, 0.5)},
            {"properties": {"id": "no-file", "urls": {}}, "geometry": square(WEST, SOUTH, 0.1)},
        ]
    }  # fmt: skip


def block_answer() -> dict[str, Any]:
    features = []
    for r in range(4):
        for c in range(4):
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "GEOID": f"310010001001{r}{c:02d}",
                        "STATE": "31",
                        "COUNTY": "001",
                        "TRACT": "000100",
                        "BLOCK": f"1{r}{c:02d}",
                        "POP100": 0 if (r, c) == (0, 0) else 40,
                        "HU100": 0 if (r, c) == (0, 0) else 15,
                    },
                    "geometry": square(WEST + c * STEP, SOUTH + r * STEP, STEP),
                }
            )
    return {"type": "FeatureCollection", "features": features}


def dhc_answer(variables: list[str]) -> list[list[str]]:
    rows = [[*variables, "state", "county", "tract", "block"]]
    for r in range(4):
        for c in range(4):
            values = ["2" if v.startswith("P12") else "14" for v in variables]
            rows.append([*values, "31", "001", "000100", f"1{r}{c:02d}"])
    return rows


def elevation_answer(points: list[list[float]]) -> dict[str, Any]:
    samples = [
        {"locationId": i, "value": "NoData" if i == 0 else f"{1000 + 10 * (y - SOUTH) / STEP:.3f}"}
        for i, (_x, y) in enumerate(points)
    ]
    return {"samples": samples}


def hpms_services() -> dict[str, Any]:
    names = ["HPMS_FULL_NE_2024", "HPMS_FULL_NE_2025", "HPMS_FULL_IA_2025", "Other_NE_2030"]
    return {"services": [{"name": f"Hosted/{n}", "type": "FeatureServer"} for n in names]}


def hpms_page(offset: int) -> dict[str, Any]:
    """Counted sections, one per page: Main (row 2, east-west), then column 3."""
    main = [
        [WEST - STEP, SOUTH + 2 * STEP + 0.00005],
        [WEST + 5 * STEP, SOUTH + 2 * STEP + 0.00005],
    ]
    column = [[WEST + 3 * STEP, SOUTH], [WEST + 3 * STEP, SOUTH + 4 * STEP]]
    pages = [
        ({"aadt": 13000, "f_system": 3, "route_id": "MAIN", "data_year": 2025}, main),
        ({"aadt": 3000, "f_system": 5, "route_id": "C3", "data_year": 2025}, column),
    ]
    if offset >= len(pages):
        return {"type": "FeatureCollection", "features": []}
    properties, line = pages[offset]
    feature = {
        "type": "Feature",
        "properties": properties,
        "geometry": {"type": "LineString", "coordinates": line},
    }
    return {"type": "FeatureCollection", "features": [feature],
            "properties": {"exceededTransferLimit": offset + 1 < len(pages)}}  # fmt: skip


Handler = Callable[[httpx.Request], httpx.Response]


def handler(calls: list[str]) -> Handler:
    """Answer each service as its real counterpart would, recording the hosts called."""

    def answer(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(request.url.host + request.url.path)
        if "Places_CouSub" in url:
            return httpx.Response(200, json=place_answer())
        if url.endswith("index-v1.json"):
            return httpx.Response(200, json=geofabrik_index())
        if url == PBF_URL:
            return httpx.Response(200, content=pbf_bytes(overpass_answer()))
        if "Tracts_Blocks" in url:
            offset = int(request.url.params.get("resultOffset", "0"))
            return httpx.Response(200, json=block_answer() if offset == 0 else {"features": []})
        if "api.census.gov" in url:
            variables = request.url.params["get"].split(",")
            return httpx.Response(200, json=dhc_answer(variables))
        if "State_County" in url:
            return httpx.Response(200, json={"features": [{"attributes": {"STUSAB": "NE"}}]})
        if request.url.path.endswith("/services/Hosted"):
            return httpx.Response(200, json=hpms_services())
        if "HPMS_FULL_NE_2025" in url:
            return httpx.Response(200, json=hpms_page(int(request.url.params["resultOffset"])))
        if "getSamples" in url:
            form = parse_qs(request.content.decode())
            points = json.loads(form["geometry"][0])["points"]
            return httpx.Response(200, json=elevation_answer(points))
        return httpx.Response(404, text="unknown service")

    return answer


def fetcher(cache: Path, calls: list[str]) -> Fetcher:
    client = httpx.Client(transport=httpx.MockTransport(handler(calls)))
    return Fetcher(cache, client, backoff=wait_none())


OVERTURE_RELEASE = "2026-10-21.0"


def overture_buildings(root: Path) -> None:
    """A made-up Overture release: homes in two blocks, a garage, and a shop.

    Block (1, 1) gets a house and a house a quarter its size, plus a garage;
    block (2, 2) gets a four-storey apartment block; block (3, 3) only a shop,
    so it keeps its single point.  An older, empty release sits beside it.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    import shapely

    def box(r: int, c: int, dx: float, dy: float, size: float) -> Any:
        x, y = WEST + c * STEP + dx, SOUTH + r * STEP + dy
        return shapely.box(x, y, x + size, y + size)

    rows = [
        ("house-big", "residential", "house", None, None, box(1, 1, 0.0003, 0.0003, 0.0002)),
        ("house-small", "residential", "house", None, None, box(1, 1, 0.0010, 0.0010, 0.0001)),
        ("garage", "outbuilding", "garage", None, None, box(1, 1, 0.0015, 0.0003, 0.0001)),
        ("flats", None, "apartments", 4, None, box(2, 2, 0.0005, 0.0005, 0.0002)),
        ("shop", "commercial", "retail", None, 6.0, box(3, 3, 0.0005, 0.0005, 0.0003)),
    ]
    geometries = [row[5] for row in rows]
    table = pa.table({
        "id": [r[0] for r in rows],
        "subtype": pa.array([r[1] for r in rows], pa.string()),
        "class": pa.array([r[2] for r in rows], pa.string()),
        "num_floors": pa.array([r[3] for r in rows], pa.int32()),
        "height": pa.array([r[4] for r in rows], pa.float64()),
        "geometry": pa.array(list(shapely.to_wkb(geometries)), pa.binary()),
        "bbox": pa.array(
            [dict(zip(("xmin", "ymin", "xmax", "ymax"), g.bounds, strict=True)) for g in geometries],
            pa.struct([("xmin", pa.float64()), ("xmax", pa.float64()),
                       ("ymin", pa.float64()), ("ymax", pa.float64())]),
        ),
    })  # fmt: skip
    folder = root / OVERTURE_RELEASE / "theme=buildings" / "type=building"
    folder.mkdir(parents=True)
    pq.write_table(table, folder / "part-0.parquet")
    (root / "2026-09-23.0").mkdir()
