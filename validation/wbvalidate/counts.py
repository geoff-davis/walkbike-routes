"""Walking and cycling counts in one format, for comparing with modelled flows.

A counts file has one row per count site, as GeoParquet, GeoJSON or any file
GeoPandas reads, with point geometry and these columns:

- ``site_id``: unique text;
- ``mode``: ``walk`` or ``bike``;
- ``volume``: people per average day (or any measure that is the same for
  every site, since only ranks are compared);
- ``year``: when it was counted;
- ``road``: the street the counter is on, as named locally (may be blank);
- ``direction``: ``both`` when both directions were counted, otherwise the one
  direction counted (``north``, ``east``, ...);
- ``side``: for walking, which sidewalk (``north``, ``east``, ...), else blank;
- ``facility``: the source's own facility label, for breakdowns (may be blank);
- ``source``: who counted.

Counts are real records: keep them out of the repository, and publish only
figures computed from them, with the source's credit.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from walkbike_routes.exceptions import AccessibilityError

COLUMNS = (
    "site_id", "mode", "volume", "year", "road", "direction", "side", "facility", "source"
)  # fmt: skip
TEXT = ("site_id", "road", "direction", "side", "facility", "source")


class CountsError(AccessibilityError):
    """A counts file that cannot be used."""


def read(path: Path) -> gpd.GeoDataFrame:
    """Read and check a counts file."""
    try:
        parquet = path.suffix.lower() in {".parquet", ".geoparquet"}
        frame = gpd.read_parquet(path) if parquet else gpd.read_file(path)
    except (OSError, ValueError) as error:
        raise CountsError(f"{path}: not a readable counts file ({error})") from error
    return check(frame)


def check(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """The counts with their columns checked and tidied; raises :class:`CountsError`."""
    missing = [c for c in COLUMNS if c not in frame.columns]
    if missing:
        raise CountsError(f"counts: missing columns {missing}")
    if frame.crs is None:
        raise CountsError("counts: no coordinate system")
    if not frame.geometry.geom_type.eq("Point").all():
        raise CountsError("counts: every site must be a point")
    out = frame[[*COLUMNS, "geometry"]].copy()
    for name in TEXT:
        out[name] = out[name].fillna("").astype(str).str.strip()
    out["mode"] = out["mode"].astype(str)
    bad = sorted(set(out["mode"]) - {"walk", "bike"})
    if bad:
        raise CountsError(f"counts: mode must be walk or bike, got {bad}")
    volume = pd.to_numeric(out.volume, errors="coerce")
    if volume.isna().any() or not np.isfinite(volume).all() or (volume < 0).any():
        raise CountsError("counts: volume must be a number, 0 or more")
    out["volume"] = volume.astype(float)
    out["year"] = pd.to_numeric(out.year, errors="coerce").astype("Int64")
    if out.site_id.eq("").any() or out.site_id.duplicated().any():
        raise CountsError("counts: site_id must be filled in and unique")
    out["direction"] = out.direction.str.lower().replace("", "both")
    return gpd.GeoDataFrame(out.reset_index(drop=True), geometry="geometry", crs=frame.crs)
