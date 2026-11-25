"""Build a real study's layers from public data, in the shape the demo town has.

Streets come from OpenStreetMap, with any local layers (a city's sidewalks,
signals, speeds or bikeways) laid over them, homes from 2020 Census blocks and elevation
from USGS 3DEP; destinations and their entrances come from the study config.
Each populated block's counts are spread over its buildings from Overture
Maps (see :mod:`walkbike_routes.demand.spread`), or kept as one point per
block with ``buildings: none``.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from walkbike_routes import config as settings
from walkbike_routes import local_layers, overrides, switrs
from walkbike_routes.demand.spread import spread_blocks
from walkbike_routes.demo import Town
from walkbike_routes.download import area as areas
from walkbike_routes.download import census, elevation, hpms, lodes, osm, overture
from walkbike_routes.download.http import Fetcher
from walkbike_routes.network import osm_streets, traffic

log = logging.getLogger(__name__)


def build(study: settings.Study, data: Path, fetcher: Fetcher | None = None) -> Town:
    """The study's nodes, links, homes, destinations and access points."""
    fetcher = fetcher or Fetcher(data)
    place = areas.resolve(study.area, fetcher, study.crs)
    log.info("area: %s (%s), working in %s", place.name, place.source, place.crs)
    _check_inside(study, place)
    bbox = place.bbox_4326()
    demands = {settings.demand_for(study, d) for d in study.destinations} | {study.demand}
    # Before the streets, the largest download: a missing Census key stops the run here.
    blocks = _blocks(sorted(demands, key=repr), bbox, fetcher)
    elements = osm.elements(bbox, fetcher, study.area.osm_file)
    sources: dict[str, object] = {
        # A file of the study's own is named, never quoted: it may say where it came from.
        "openstreetmap": {"file": study.area.osm_file.name}
        if study.area.osm_file is not None
        else elements.get("source"),
        "census": "2020 Census blocks (TIGERweb); Demographic and Housing Characteristics",
    }
    links, nodes = osm_streets.streets(elements)
    links, nodes = _clip(links.to_crs(place.crs), nodes.to_crs(place.crs), place.extent)
    log.info("streets: %d links, %d nodes", len(links), len(nodes))
    if study.local_layers:
        links, nodes, counts = local_layers.apply(links, nodes, study.local_layers)
        log.info("local layers: %s", local_layers.describe(counts))
    if study.area.traffic_counts == "hpms":
        links = traffic.match_counts(links, hpms.segments(traffic.search_bbox(links), fetcher))
        table = traffic.coverage(links)
        log.info(
            "traffic counts: %.0f%% of street length; by class %s",
            100 * table.counted_km.sum() / max(table.km.sum(), 1e-9),
            ", ".join(f"{k} {v:.0%}" for k, v in table.share_counted.items()),
        )
    if study.overrides is not None:
        corrections = overrides.load(study.overrides)
        links, nodes = overrides.apply(links, nodes, corrections)
        log.info("overrides: %d applied from %s", len(corrections), study.overrides.name)
    nodes = elevation.node_elevations(nodes, fetcher)
    homes, sources["overture_release"] = _homes(study.area, blocks, place, fetcher)
    log.info("homes: %d, from %d populated blocks", len(homes), homes.block_id.nunique())
    destinations, access_points = _destinations(study, place.crs)
    jobs = None
    if study.network_flows is not None:
        jobs = _jobs(bbox, place, fetcher, study.network_flows.jobs_year)
        sources["lodes_year"] = study.network_flows.jobs_year
        log.info("jobs: %d in %d blocks", int(jobs.jobs.sum()), len(jobs))
    return Town(
        nodes=nodes,
        links=links,
        homes=homes,
        destinations=destinations,
        access_points=access_points,
        jobs=jobs,
        sources=sources,
    )


def _check_inside(study: settings.Study, place: areas.StudyArea) -> None:
    """Refuse a destination or entrance outside the area, before anything large is fetched."""
    extent = place.extent.to_crs(4326).union_all()
    for destination in study.destinations:
        named = [("location", destination.location)] if destination.location else []
        named += [("access point", p) for p in destination.access_points]
        named += [("car access point", p) for p in destination.car_access_points]
        for what, lonlat in named:
            if not extent.covers(Point(lonlat)):
                raise settings.ConfigError(
                    f"destination {destination.id!r}: {what} [{lonlat[0]}, {lonlat[1]}] is outside "
                    "the study area; it is [longitude, latitude], and area.buffer_m widens the area"
                )


def _jobs(
    bbox: tuple[float, float, float, float], place: areas.StudyArea, fetcher: Fetcher, year: int
) -> gpd.GeoDataFrame:
    """LODES jobs at a point inside each block with jobs, within the study extent."""
    blocks = census.blocks(bbox, fetcher)
    counts = lodes.workplace_jobs(sorted(set(blocks.STATE.astype(str))), fetcher, year)
    found = blocks[["block_id", "geometry"]].merge(counts, on="block_id")
    found = found[found.jobs.gt(0)].to_crs(place.crs)
    extent = place.extent.union_all()
    found = found[found.intersects(extent)].copy()
    # A block reaching past the extent keeps all its jobs, at a point on its part inside.
    found["geometry"] = found.geometry.intersection(extent).representative_point()
    return gpd.GeoDataFrame(
        found[["block_id", "jobs", "geometry"]].reset_index(drop=True),
        geometry="geometry",
        crs=place.crs,
    )


def _clip(
    links: gpd.GeoDataFrame, nodes: gpd.GeoDataFrame, extent: gpd.GeoDataFrame
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Keep links that touch the extent, and the nodes they use."""
    outline = extent.union_all()
    kept = links[links.intersects(outline)].reset_index(drop=True)
    used = set(kept.u) | set(kept.v)
    return kept, nodes[nodes.node_id.isin(used)].reset_index(drop=True)


def _blocks(
    demands: list[settings.Demand], bbox: tuple[float, float, float, float], fetcher: Fetcher
) -> gpd.GeoDataFrame:
    """The Census blocks touching the box, with every count the study's demands use."""
    blocks = census.blocks(bbox, fetcher)
    for demand in demands:  # one count column for each age range any destination uses
        if demand.weight == "households":
            blocks = census.add_counts(blocks, fetcher, households=True)
        elif demand.min_age is not None or demand.max_age is not None:
            blocks = census.add_counts(
                blocks, fetcher, min_age=demand.min_age, max_age=demand.max_age
            )
    return blocks


def _homes(
    area: settings.Area, blocks: gpd.GeoDataFrame, place: areas.StudyArea, fetcher: Fetcher
) -> tuple[gpd.GeoDataFrame, str | None]:
    """Homes from the blocks' counts, and the Overture release their buildings came from."""
    blocks = blocks.to_crs(place.crs)
    blocks = blocks[blocks.residents.gt(0) & blocks.intersects(place.extent.union_all())]
    blocks = blocks.drop(columns=["STATE", "COUNTY", "TRACT", "BLOCK"]).reset_index(drop=True)
    counts = [c for c in blocks.columns if c not in {"block_id", "geometry"}]
    if area.buildings == "none":
        found = gpd.GeoDataFrame(geometry=[], crs=place.crs)
        source = ""
        release = None
    else:
        # Read the whole of every block kept, which can reach past the study box.
        west, south, east, north = blocks.to_crs(4326).total_bounds
        found, release = overture.buildings(
            (float(west), float(south), float(east), float(north)),
            fetcher.cache,
            release=area.overture_release,
        )
        found = found.to_crs(place.crs)
        source = f"Overture Maps {release}"
    homes = spread_blocks(blocks, found, counts=counts)
    homes["building_source"] = [source if p == "building" else "" for p in homes.placed_by]
    return homes, release


def _shapes(path: Path) -> gpd.GeoDataFrame:
    try:
        return gpd.read_file(path)
    except (OSError, ValueError, RuntimeError) as err:  # pyogrio raises RuntimeErrors
        raise settings.ConfigError(f"could not read {path}: {err}") from err


def outline(path: Path, crs: str) -> BaseGeometry:
    """The union of the shapes in a boundary file, in ``crs``."""
    shapes = _shapes(path)
    if shapes.crs is None:
        raise settings.ConfigError(f"{path}: the boundary has no coordinate system")
    return shapes.to_crs(crs).union_all()


def outline_shape(destination: settings.Destination, crs: str) -> BaseGeometry:
    """The destination's outline: its file's shapes that ``where`` keeps, joined, in ``crs``."""
    drawn = destination.outline
    assert drawn is not None
    shapes = _shapes(drawn.file)
    if shapes.crs is None:
        raise settings.ConfigError(f"{drawn.file}: the outline has no coordinate system")
    for column, allowed in drawn.where.items():
        if column not in shapes:
            raise settings.ConfigError(f"{drawn.file}: no column {column!r}")
        shapes = shapes[shapes[column].astype(str).str.strip().isin(allowed)]
    if shapes.empty:
        raise settings.ConfigError(
            f"{destination.id}: no shape in {drawn.file.name} matches the outline's where"
        )
    return shapes.to_crs(crs).union_all()


def outlines(study: settings.Study, crs: str) -> gpd.GeoDataFrame:
    """The shapes drawn on each destination's map: its outline and its serves area.

    ``served`` is true for a serves area or a limiting outline, whose homes
    alone make trips there.
    """
    rows: list[dict[str, object]] = []
    for destination in study.destinations:
        if destination.outline is not None:
            rows.append(
                {
                    "destination_id": destination.id,
                    "name": destination.outline.name,
                    "served": destination.outline.limits,
                    "geometry": outline_shape(destination, crs),
                }
            )
        if destination.serves is not None:
            rows.append(
                {
                    "destination_id": destination.id,
                    "name": "Homes counted",
                    "served": True,
                    "geometry": outline(destination.serves, crs),
                }
            )
    columns = ["destination_id", "name", "served", "geometry"]
    return gpd.GeoDataFrame(rows, columns=columns, geometry="geometry", crs=crs)


def crash_forecast(study: settings.Study, crs: str, bounds: BaseGeometry) -> gpd.GeoDataFrame:
    """The study's forecast crash corners within ``bounds``, most expected first.

    Read from the private config's ``crash_forecast`` file: points with
    ``rank`` and ``expected_bike_crashes``, and ``streets`` and
    ``observed_bike_crashes`` when present.  Drawn on the maps only.
    """
    columns = [
        "rank",
        "streets",
        "expected_bike_crashes",
        "observed_bike_crashes",
        "first_year",
        "last_year",
        "geometry",
    ]
    if study.crash_forecast is None:
        return gpd.GeoDataFrame(columns=columns, geometry="geometry", crs=crs)
    name = study.crash_forecast.name
    read = gpd.read_file(study.crash_forecast)
    missing = [c for c in ("rank", "expected_bike_crashes") if c not in read.columns]
    if missing:
        raise settings.ConfigError(f"crash_forecast: {name} has no {missing[0]} column")
    if read.crs is None:
        raise settings.ConfigError(f"crash_forecast: {name} gives no coordinate system")
    read = read[read.geometry.notna() & read.geometry.geom_type.eq("Point")].to_crs(crs)
    read = read[read.intersects(bounds)].copy()  # a corner on the edge counts
    for column in ("rank", "expected_bike_crashes", "observed_bike_crashes"):
        given = read[column] if column in read else pd.Series(math.nan, index=read.index)
        read[column] = pd.to_numeric(given, errors="coerce")
    read["streets"] = read.get("streets", pd.Series("", index=read.index)).fillna("").astype(str)
    read = read.dropna(subset=["expected_bike_crashes"])
    read = read.sort_values(["expected_bike_crashes", "rank"], ascending=[False, True])
    # The years its expected crashes cover, when the private file gives them.
    years = study.crash_forecast_years or (None, None)
    read["first_year"] = pd.Series(years[0], index=read.index, dtype="Int64")
    read["last_year"] = pd.Series(years[1], index=read.index, dtype="Int64")
    logging.info("crash forecast: %d corners within the streets", len(read))
    return gpd.GeoDataFrame(read[columns], geometry="geometry", crs=crs)


def recorded_crashes(study: settings.Study, crs: str, bounds: BaseGeometry) -> gpd.GeoDataFrame:
    """The study's police-recorded bike crashes within ``bounds``, worst drawn last.

    Read from the private config's ``recorded_crashes`` files, in the
    California SWITRS layout (:mod:`walkbike_routes.switrs`).  Drawn on the
    maps only.
    """
    if study.recorded_crashes is None:
        return gpd.GeoDataFrame(columns=switrs.COLUMNS, geometry="geometry", crs=crs)
    read = switrs.bike_crashes(study.recorded_crashes, study.crash_parties).to_crs(crs)
    read = read[read.intersects(bounds)]
    logging.info("recorded crashes: %d bike crashes within the streets", len(read))
    return gpd.GeoDataFrame(read, geometry="geometry", crs=crs)


def _destinations(study: settings.Study, crs: str) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    rows: list[dict[str, object]] = []
    gates: list[dict[str, object]] = []
    for destination in study.destinations:
        if destination.kind == "point":
            assert destination.location is not None  # the config requires it
            shape = gpd.GeoSeries([Point(destination.location)], crs=4326).to_crs(crs).iloc[0]
        else:
            assert destination.boundary_file is not None
            shape = outline(destination.boundary_file, crs)
        rows.append(
            {
                "destination_id": destination.id,
                "name": destination.name,
                "kind": destination.kind,
                "geometry": shape,
            }
        )
        for index, location in enumerate(destination.access_points):
            gates.append(
                {
                    "access_point_id": f"{destination.id}_{index + 1}",
                    "destination_id": destination.id,
                    "car": False,
                    "geometry": Point(location),
                }
            )
        for index, location in enumerate(destination.car_access_points):
            gates.append(
                {
                    "access_point_id": f"{destination.id}_car_{index + 1}",
                    "destination_id": destination.id,
                    "car": True,
                    "geometry": Point(location),
                }
            )
    destinations = gpd.GeoDataFrame(
        pd.DataFrame(rows, columns=["destination_id", "name", "kind", "geometry"]),
        geometry="geometry",
        crs=crs,
    )
    access = gpd.GeoDataFrame(
        pd.DataFrame(gates, columns=["access_point_id", "destination_id", "car", "geometry"]),
        geometry="geometry",
        crs=4326,
    ).to_crs(crs)
    return destinations, access
