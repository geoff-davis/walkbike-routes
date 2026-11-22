"""Recorded bicycle trips from CyclePhilly, published by DVRPC.

CyclePhilly was a smartphone app, built by Code for Philly, for recording
bicycle trips in the Philadelphia region.  The Delaware Valley Regional
Planning Commission publishes its trips from May 2014 to April 2016
(https://www.dvrpc.org/webmaps/cyclephilly/): about 12,000 trips by about
300 riders, each a line snapped by DVRPC to its 2010 OpenStreetMap streets,
with the trip ends blurred to protect riders' privacy.  DVRPC's notes say the
riders may not ride like cyclists in general.  Credit Code for Philly and
DVRPC wherever results are used.

The trips become the traces format (:mod:`wbvalidate.traces`):
``TripID``, ``UserId``, ``Purpose`` and ``rider_type`` (the rider's own
answer, blank where they gave none).  Riders' other answers are not kept.
"""

from __future__ import annotations

import zipfile

import geopandas as gpd

from walkbike_routes.download.http import DownloadError, Fetcher
from wbvalidate.traces import check

URL = "https://www.dvrpc.org/webmaps/cyclephilly/data/tripbytrip_geojson.zip"
CREDIT = (
    "Recorded bicycle trips: CyclePhilly (Code for Philly), published by the Delaware Valley "
    "Regional Planning Commission (DVRPC)"
)
NO_ANSWER = "no data"


def trips(fetcher: Fetcher) -> gpd.GeoDataFrame:
    """Every CyclePhilly trip, in the traces format."""
    path = fetcher.download(URL, "cyclephilly-tripbytrip-geojson.zip")
    try:
        with zipfile.ZipFile(path) as archive:
            members = [n for n in archive.namelist() if n.lower().endswith(".geojson")]
        if len(members) != 1:
            raise DownloadError(f"{path.name}: expected one GeoJSON file, found {members}")
        records = gpd.read_file(f"zip://{path}!{members[0]}")
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        raise DownloadError(f"{path.name}: not the CyclePhilly trips ({error})") from error
    return frame(records)


def frame(records: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """CyclePhilly's trip records in the traces format."""
    needed = {"TripID", "UserId", "Purpose", "rider_type", "geometry"}
    missing = sorted(needed - set(records.columns))
    if missing:
        raise DownloadError(f"CyclePhilly trips: missing columns {missing}")
    group = records.rider_type.fillna("").astype(str).str.strip()
    out = gpd.GeoDataFrame(
        {
            "trip_id": records.TripID.astype(str),
            "user_id": records.UserId.astype(str),
            "purpose": records.Purpose.fillna("").astype(str),
            "group": group.mask(group.str.lower().eq(NO_ANSWER), ""),
        },
        geometry=records.geometry.values,
        crs=records.crs,
    )
    return check(out)
