"""Building footprints from Overture Maps, read straight from its open GeoParquet.

Overture's buildings join OpenStreetMap with machine-traced footprints
(Microsoft, Google, Esri), so they cover places where OSM has few buildings.
The data are ODbL; credit "© OpenStreetMap contributors, Overture Maps
Foundation".  Only the rows whose bounding box meets the study box are read,
and the result is cached per release and box.
"""

from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.fs as pafs
import shapely

from walkbike_routes.download.http import DownloadError

log = logging.getLogger(__name__)

BUCKET = "overturemaps-us-west-2/release"
REGION = "us-west-2"
COLUMNS = ["id", "subtype", "class", "num_floors", "height", "geometry"]
READERS = 8  # files read at once


def _filesystem() -> pafs.FileSystem:
    return pafs.S3FileSystem(anonymous=True, region=REGION)


def latest_release(filesystem: pafs.FileSystem | None = None, root: str | None = None) -> str:
    """The newest release name, such as ``2026-10-21.0``."""
    filesystem = filesystem or _filesystem()
    root = root or BUCKET
    try:
        names = sorted(
            info.base_name
            for info in filesystem.get_file_info(pafs.FileSelector(root))
            if info.type == pafs.FileType.Directory
        )
    except OSError as err:
        raise DownloadError(f"could not list Overture releases: {err}") from err
    if not names:
        raise DownloadError("no Overture release found")
    return str(names[-1])


def _read(filesystem: pafs.FileSystem, folder: str, near: pc.Expression) -> pa.Table:
    """The matching rows of every file under ``folder``.

    Each file is opened on its own and let go once read.  A whole-release
    ``pyarrow.dataset`` keeps every file's footer in memory, about 4 GB for
    one city's worth of buildings; this way peak memory is a few hundred MB.
    """

    def hidden(path: str) -> bool:  # like pyarrow.dataset: skip "_" and "." files and folders
        return any(part.startswith(("_", ".")) for part in path[len(folder) :].split("/") if part)

    files = sorted(
        info.path
        for info in filesystem.get_file_info(pafs.FileSelector(folder, recursive=True))
        if info.type == pafs.FileType.File and not hidden(info.path)
    )
    parquet = ds.ParquetFileFormat()

    def one(path: str) -> list[pa.Table]:
        fragment = parquet.make_fragment(path, filesystem=filesystem)
        return [
            group.to_table(columns=COLUMNS, filter=near, use_threads=False)
            for group in fragment.split_by_row_group(filter=near)
        ]

    with ThreadPoolExecutor(max_workers=READERS) as pool:
        parts = [t for tables in pool.map(one, files) for t in tables if t.num_rows]
    if not parts:
        if not files:
            raise ValueError(f"no files under {folder}")
        schema = parquet.make_fragment(files[0], filesystem=filesystem).physical_schema
        return pa.Table.from_pylist([], schema=pa.schema([schema.field(c) for c in COLUMNS]))
    return pa.concat_tables(parts, promote_options="permissive")  # files may differ in int width


def buildings(
    bbox: tuple[float, float, float, float],
    cache: Path,
    *,
    release: str | None = None,
    filesystem: pafs.FileSystem | None = None,
    root: str | None = None,
) -> tuple[gpd.GeoDataFrame, str]:
    """Footprints meeting ``bbox`` (west, south, east, north), and the release read."""
    filesystem = filesystem or _filesystem()
    root = root or BUCKET
    box = [float(v) for v in bbox]
    # The release last read for this box, so a run with no network can use it again.
    marker = cache / f"overture-release-{_digest([root, box])}.txt"
    listed = release is None
    if release is None:
        try:
            release = latest_release(filesystem, root)
        except DownloadError:
            release = marker.read_text().strip() if marker.is_file() else ""
            if not release or not _saved(cache, root, release, box).is_file():
                raise
            log.warning("buildings: could not list Overture releases; using saved %s", release)
    path = _saved(cache, root, release, box)
    if path.exists():
        try:
            found = gpd.read_parquet(path)
        except (OSError, ValueError) as err:  # pyarrow's errors are these
            log.warning("buildings: %s is damaged (%s); reading it again", path.name, err)
            path.unlink()
        else:
            if listed:
                marker.write_text(release + "\n")
            return found, release
    west, south, east, north = bbox
    near = (
        (pc.field("bbox", "xmin") <= east)
        & (pc.field("bbox", "xmax") >= west)
        & (pc.field("bbox", "ymin") <= north)
        & (pc.field("bbox", "ymax") >= south)
    )
    log.info("buildings: reading Overture %s (this can take a minute)", release)
    try:
        table = _read(filesystem, f"{root}/{release}/theme=buildings/type=building", near)
    except (OSError, ValueError) as err:
        raise DownloadError(f"could not read Overture buildings {release}: {err}") from err
    frame: dict[str, Any] = {
        "building_id": table["id"].to_pylist(),
        "subtype": table["subtype"].to_pylist(),
        "class": table["class"].to_pylist(),
        "num_floors": pd.to_numeric(pd.Series(table["num_floors"].to_pylist()), errors="coerce"),
        "height": pd.to_numeric(pd.Series(table["height"].to_pylist()), errors="coerce"),
    }
    geometry = shapely.from_wkb(table["geometry"].to_numpy(zero_copy_only=False))
    result = gpd.GeoDataFrame(frame, geometry=geometry, crs=4326)
    result = result[result.geometry.notna() & result.geom_type.isin(["Polygon", "MultiPolygon"])]
    result = result.reset_index(drop=True)
    cache.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    result.to_parquet(part)
    part.replace(path)
    if listed:
        marker.write_text(release + "\n")
    return result, release


def _digest(key: list[Any]) -> str:
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16]


def _saved(cache: Path, root: str, release: str, box: list[float]) -> Path:
    return cache / f"overture-buildings-{_digest([root, release, box])}.parquet"
