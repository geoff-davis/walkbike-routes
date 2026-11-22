"""2020 Census blocks with the people who live in them.

Block outlines, total residents (``POP100``) and housing units (``HU100``) come
from TIGERweb and need no key.  Residents of particular ages and occupied
households come from the 2020 Demographic and Housing Characteristics file
(DHC), whose API needs a free key in ``CENSUS_API_KEY``
(https://api.census.gov/data/key_signup.html).

Ages come in the DHC's bands (P12: under 5, 5 to 9, 10 to 14, 15 to 17, ...).
When a study's age range cuts a band, residents are taken to be spread evenly
over the band's years.
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Any

import geopandas as gpd
import pandas as pd

from walkbike_routes.download.http import DownloadError, Fetcher

TIGERWEB_BLOCKS = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Tracts_Blocks/MapServer/12"
)
DHC = "https://api.census.gov/data/2020/dec/dhc"
PAGE = 2000

# P12 age bands: (first age, last age, male variable number). Female is +24.
AGE_BANDS = [
    (0, 4, 3), (5, 9, 4), (10, 14, 5), (15, 17, 6), (18, 19, 7), (20, 20, 8),
    (21, 21, 9), (22, 24, 10), (25, 29, 11), (30, 34, 12), (35, 39, 13),
    (40, 44, 14), (45, 49, 15), (50, 54, 16), (55, 59, 17), (60, 61, 18),
    (62, 64, 19), (65, 66, 20), (67, 69, 21), (70, 74, 22), (75, 79, 23),
    (80, 84, 24), (85, 120, 25),
]  # fmt: skip


class CensusKeyMissing(DownloadError):
    """Ages and households need a Census API key."""


def blocks(bbox: tuple[float, float, float, float], fetcher: Fetcher) -> gpd.GeoDataFrame:
    """Every 2020 block touching a west, south, east, north box, in degrees."""
    west, south, east, north = bbox
    features: list[dict[str, object]] = []
    offset = 0
    while True:
        answer = fetcher.json(
            f"{TIGERWEB_BLOCKS}/query",
            {
                "geometry": f"{west},{south},{east},{north}",
                "geometryType": "esriGeometryEnvelope",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "GEOID,STATE,COUNTY,TRACT,BLOCK,POP100,HU100",
                "returnGeometry": "true",
                "outSR": "4326",
                "orderByFields": "GEOID",
                "resultOffset": str(offset),
                "resultRecordCount": str(PAGE),
                "f": "geojson",
            },
            name="blocks",
            check=lambda a: None if isinstance(a, dict) and "features" in a else "no features",
        )
        page = answer["features"]
        features.extend(page)
        # The server may send fewer than asked for and say there are more.
        more = answer.get("exceededTransferLimit") or (answer.get("properties") or {}).get(
            "exceededTransferLimit"
        )
        if not page or (len(page) < PAGE and not more):
            break
        offset += len(page)
    columns = ["GEOID", "STATE", "COUNTY", "TRACT", "BLOCK", "POP100", "HU100"]
    if not features:
        return gpd.GeoDataFrame(columns=[*columns, "geometry"], geometry="geometry", crs=4326)
    frame = gpd.GeoDataFrame.from_features(features, crs=4326)
    frame = frame.drop_duplicates("GEOID").reset_index(drop=True)
    frame["POP100"] = frame.POP100.astype(int)
    frame["HU100"] = frame.HU100.astype(int)
    return frame[[*columns, "geometry"]].rename(
        columns={"GEOID": "block_id", "POP100": "residents", "HU100": "housing_units"}
    )


def age_weights(first: int, last: int) -> dict[str, float]:
    """DHC variables, and the share of each, that make up residents aged first to last."""
    weights: dict[str, float] = {}
    for low, high, male in AGE_BANDS:
        overlap = min(high, last) - max(low, first) + 1
        if overlap <= 0:
            continue
        share = overlap / (high - low + 1)
        weights[f"P12_{male:03d}N"] = share
        weights[f"P12_{male + 24:03d}N"] = share
    return weights


def dhc_by_block(
    counties: list[tuple[str, str]], variables: list[str], fetcher: Fetcher
) -> pd.DataFrame:
    """DHC counts by block for whole counties (state FIPS, county FIPS).

    A table already in the cache is used as it is; the key is needed only to
    ask for one that is not.
    """
    key = os.environ.get("CENSUS_API_KEY", "").strip()

    def problem(answer: Any) -> str | None:
        return None if isinstance(answer, list) and len(answer) >= 1 else "not a table"

    frames: list[pd.DataFrame] = []
    for state, county in counties:
        asked = {
            "get": ",".join(variables),
            "for": "block:*",
            "in": f"state:{state} county:{county} tract:*",
        }
        name = f"dhc-{state}{county}"
        table = fetcher.cached(DHC, asked, name=name, check=problem)
        if table is None and not key:
            raise CensusKeyMissing(
                "residents by age and households need a free Census API key: get one at "
                "https://api.census.gov/data/key_signup.html and set CENSUS_API_KEY"
            )
        if table is None:
            table = fetcher.json(DHC, asked, secret_params={"key": key}, name=name, check=problem)
        header, *rows = table
        frame = pd.DataFrame(rows, columns=header)
        frame["block_id"] = frame.state + frame.county + frame.tract + frame.block
        frames.append(frame[["block_id", *variables]])
    result = pd.concat(frames, ignore_index=True)
    for name in variables:
        result[name] = pd.to_numeric(result[name])
    return result


def add_counts(
    frame: gpd.GeoDataFrame,
    fetcher: Fetcher,
    *,
    min_age: int | None = None,
    max_age: int | None = None,
    households: bool = False,
) -> gpd.GeoDataFrame:
    """Add ``residents_<min>_<max>`` and/or ``households`` columns from the DHC."""
    weights = age_weights(min_age or 0, max_age if max_age is not None else 120)
    wanted = min_age is not None or max_age is not None
    variables = (sorted(weights) if wanted else []) + (["H1_002N"] if households else [])
    if not variables:
        return frame
    counties = sorted(set(zip(frame.STATE.astype(str), frame.COUNTY.astype(str), strict=True)))
    table = dhc_by_block(counties, variables, fetcher).set_index("block_id")
    result = frame.copy()
    if wanted:
        column = f"residents_{min_age or 0}_{max_age if max_age is not None else 'plus'}"
        totals: defaultdict[str, float] = defaultdict(float)
        for name, share in weights.items():
            for block_id, value in table[name].items():
                totals[str(block_id)] += share * float(value)
        result[column] = result.block_id.map(totals).fillna(0.0)
    if households:
        result["households"] = result.block_id.map(table["H1_002N"]).fillna(0).astype(int)
    return result
