"""Bicycle and pedestrian counts from the Delaware Valley Regional Planning Commission.

DVRPC publishes its counts for the Philadelphia region as whole layers
(https://catalog.dvrpc.org/dataset/bicycle-count-locations and
.../dvrpc-pedestrian-count-locations).  Each record is one count session:
seven days or so with tube (bicycle) or infrared (pedestrian) counters,
factored by DVRPC to an annual average daily volume (the layers are tagged
AADB and AADP).  The data come under the DVRPC Data License
(https://catalog.dvrpc.org/dvrpc_data_license.html), which asks that DVRPC be
credited wherever the data are used.

Records become count sites (:mod:`wbvalidate.counts`):

- **Sites.** Records on the same street within :data:`SITE_M` of each other
  are one site (pedestrian sites are also split by sidewalk side), since the
  two directions of a road count often sit at different points.
- **Latest session.** Each site keeps only its most recent count date.
- **Directions.** A bicycle count on a road is often one direction per
  record.  At the latest session, a record counting ``both`` directions is
  used if there is one; otherwise the distinct directions are added, and the
  site's ``direction`` is ``both`` only if opposite directions were counted.
- **Left out.** Special-event counts, and records with no volume or a
  volume of zero (usually a counter fault).
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

from walkbike_routes.download.http import DownloadError, Fetcher
from wbvalidate.counts import check
from wbvalidate.match import street_key

BASE = "https://gis.dvrpc.org/geoparquet/transportation"
LAYERS = {"bike": "bicyclecounts", "walk": "pedestriancounts"}
CREDIT = "Walking and cycling counts: Delaware Valley Regional Planning Commission (DVRPC)"
SITE_M = 40.0
OPPOSITE = ({"north", "south"}, {"east", "west"})


def counts(fetcher: Fetcher) -> gpd.GeoDataFrame:
    """Every DVRPC walking and cycling count site, in the counts format."""
    frames = []
    for mode, layer in LAYERS.items():
        path = fetcher.download(f"{BASE}/{layer}.parquet", f"dvrpc-{layer}.parquet")
        try:
            records = gpd.read_parquet(path)
        except (OSError, ValueError) as error:
            raise DownloadError(f"{path.name}: not a DVRPC count layer ({error})") from error
        frames.append(sites(records, mode))
    return check(pd.concat(frames, ignore_index=True))


def sites(records: gpd.GeoDataFrame, mode: str) -> gpd.GeoDataFrame:
    """One layer's records as count sites (``mode``: walk or bike)."""
    needed = {"road", "cntdir", "volume", "setdate", "setyear", "program", "geometry"}
    if mode == "walk":
        needed.add("sidewalk")
    missing = sorted(needed - set(records.columns))
    if missing:
        raise DownloadError(f"DVRPC {mode} counts: missing columns {missing}")
    frame = records.copy()
    frame["volume"] = pd.to_numeric(frame.volume, errors="coerce")
    frame = frame[frame.volume.gt(0) & frame.setdate.notna()]
    frame = frame[frame.program.fillna("").astype(str).ne("Spec. Event")]
    if frame.empty:
        return _empty(records.crs)
    frame = frame.to_crs(frame.estimate_utm_crs())
    frame["road_key"] = frame.road.fillna("").astype(str).map(_road)
    frame["side"] = (
        frame.sidewalk.fillna("").astype(str).str.strip().str.lower() if mode == "walk" else ""
    )
    frame["direction"] = frame.cntdir.fillna("").astype(str).str.strip().str.lower()
    frame["x"], frame["y"] = frame.geometry.x, frame.geometry.y
    frame = frame.sort_values(["road_key", "side", "x", "y", "setdate"]).reset_index(drop=True)
    frame["site"] = _group(frame)
    rows = []
    facility = "bikepedfacility" if "bikepedfacility" in frame else None
    for site, group in frame.groupby("site", sort=True):
        latest = group[group.setdate.eq(group.setdate.max())]
        volume, direction = _volume(latest)
        first = latest.iloc[0]
        rows.append(
            {
                "site_id": f"dvrpc-{mode}-{int(str(site)) + 1:05d}",
                "mode": mode,
                "volume": volume,
                "year": int(first.setyear) if pd.notna(first.setyear) else None,
                "road": _text(first.road),
                "direction": direction,
                "side": str(first.side),
                "facility": _text(first[facility]) if facility else "",
                "source": "DVRPC",
                "geometry": latest.geometry.union_all().centroid,
            }
        )
    out = gpd.GeoDataFrame(rows, geometry="geometry", crs=frame.crs).to_crs(4326)
    return out


def _empty(crs: Any) -> gpd.GeoDataFrame:
    columns = ["site_id", "mode", "volume", "year", "road", "direction", "side", "facility",
               "source"]  # fmt: skip
    return gpd.GeoDataFrame(pd.DataFrame(columns=columns), geometry=[], crs=crs).to_crs(4326)


def _group(frame: pd.DataFrame) -> list[int]:
    """Site numbers: same street key and side, within SITE_M of the site's first record."""
    found: list[tuple[str, str, float, float]] = []
    labels = []
    for road, side, x, y in zip(frame.road_key, frame.side, frame.x, frame.y, strict=True):
        for index, (r, s, fx, fy) in enumerate(found):
            if r == road and s == side and np.hypot(x - fx, y - fy) <= SITE_M:
                labels.append(index)
                break
        else:
            found.append((road, side, float(x), float(y)))
            labels.append(len(found) - 1)
    return labels


def _volume(latest: pd.DataFrame) -> tuple[float, str]:
    """A session's volume, and ``both`` or the one direction it covers."""
    both = latest[latest.direction.isin(["both", ""])]
    if not both.empty:
        return float(both.volume.mean()), "both"
    per = latest.groupby("direction").volume.mean()
    directions = set(per.index)
    two_way = any(pair <= directions for pair in OPPOSITE)
    return float(per.sum()), "both" if two_way else sorted(directions)[0]


def _text(value: object) -> str:
    return "" if value is None or bool(pd.isna(value)) else str(value)  # type: ignore[call-overload]


def _road(name: str) -> str:
    """The street, for grouping: both halves of a divided road are one site."""
    return street_key(name)
