"""Daily traffic counts from the FHWA Highway Performance Monitoring System (HPMS).

Every state reports annual average daily traffic (AADT) to the Federal Highway
Administration for every federal-aid road: interstates and freeways through
arterials and major collectors, and some minor collectors and local streets.
FHWA publishes each state's submission as a public map service, one per
year.  The latest year for each state the area touches is read.

The figures are two-way daily totals.  Off the state highways many are older
local counts that the state grows forward each year, so they are better than
a guess from road class but not a current count.
"""

from __future__ import annotations

import re

import geopandas as gpd
import pandas as pd

from walkbike_routes.download.http import DownloadError, Fetcher

SERVICES = "https://geo.dot.gov/server/rest/services/Hosted"
STATES = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/0"
PAGE = 2000
FIELDS = ("aadt", "f_system", "route_id", "data_year")


def _error(answer: object, what: str) -> str | None:
    if not isinstance(answer, dict):
        return f"{what}: not a JSON object"
    if "error" in answer:
        return f"{what} refused the request: {answer['error']}"
    return None


def _features_problem(answer: object) -> str | None:
    problem = _error(answer, "the service")
    if (
        problem is None
        and isinstance(answer, dict)
        and not isinstance(answer.get("features"), list)
    ):
        return "the answer has no features"
    return problem


def _envelope(bbox: tuple[float, float, float, float]) -> dict[str, str]:
    return {
        "geometry": ",".join(f"{v:.6f}" for v in bbox),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }


def states(bbox: tuple[float, float, float, float], fetcher: Fetcher) -> list[str]:
    """The postal codes of the states a bounding box (in degrees) touches."""
    answer = fetcher.json(
        f"{STATES}/query",
        {**_envelope(bbox), "outFields": "STUSAB", "returnGeometry": "false", "f": "json"},
        name="states",
        check=_features_problem,
    )
    return sorted({str(f["attributes"]["STUSAB"]) for f in answer["features"]})


def latest_years(fetcher: Fetcher) -> dict[str, int]:
    """The most recent year FHWA publishes for each state."""
    answer = fetcher.json(
        SERVICES, {"f": "json"}, name="hpms-services", check=lambda a: _error(a, "FHWA")
    )
    years: dict[str, int] = {}
    for service in answer.get("services", []):
        found = re.fullmatch(r"Hosted/HPMS_FULL_([A-Z]{2})_(\d{4})", str(service.get("name")))
        if found:
            state, year = found[1], int(found[2])
            years[state] = max(year, years.get(state, 0))
    return years


def segments(bbox: tuple[float, float, float, float], fetcher: Fetcher) -> gpd.GeoDataFrame:
    """HPMS road sections with a count in a bounding box, in degrees.

    Columns: ``aadt``, ``f_system`` (the federal functional class), ``route_id``,
    ``data_year`` and ``source``.
    """
    years = latest_years(fetcher)
    frames = []
    for state in states(bbox, fetcher):
        if state not in years:
            raise DownloadError(f"FHWA publishes no HPMS layer for {state}")
        layer = f"{SERVICES}/HPMS_FULL_{state}_{years[state]}/FeatureServer/0/query"
        offset = 0
        while True:
            answer = fetcher.json(
                layer,
                {
                    **_envelope(bbox),
                    "where": "aadt > 0",
                    "outFields": ",".join(FIELDS),
                    "outSR": "4326",
                    "orderByFields": "objectid",
                    "resultOffset": str(offset),
                    "resultRecordCount": str(PAGE),
                    "f": "geojson",
                },
                name="hpms",
                check=_features_problem,
            )
            features = answer["features"]
            if features:
                frame = gpd.GeoDataFrame.from_features(features, crs=4326)
                frame["source"] = f"FHWA HPMS {state} {years[state]}"
                frames.append(frame)
            more = answer.get("exceededTransferLimit") or answer.get("properties", {}).get(
                "exceededTransferLimit"
            )
            if not more:
                break
            # A spatial query may send an empty page and still say more follow.
            offset += len(features) or PAGE
    columns = [*FIELDS, "source", "geometry"]
    if not frames:
        return gpd.GeoDataFrame(columns=columns, geometry="geometry", crs=4326)
    found = pd.concat(frames, ignore_index=True)
    found = found[found.geometry.notna() & ~found.geometry.is_empty]
    return gpd.GeoDataFrame(found[columns], geometry="geometry", crs=4326)
