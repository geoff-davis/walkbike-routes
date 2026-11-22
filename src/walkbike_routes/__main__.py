"""Run a study: ``walkbike-routes run --config config/demo.yaml --out output/demo``.

``run`` builds the study's layers, runs every destination, mode and preset,
and writes the outputs with a manifest, a map per destination
(``<out>/<destination>/map.html``) and a report of tables and method
(``<out>/report.md`` and ``report.html``).  A real area is read from public
data, cached under ``--data`` (by default ``data/raw/<study>``).  ``report``
writes the maps and the report again from a finished run's outputs.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

from walkbike_routes import __version__, demo, layers, network_flows
from walkbike_routes import config as settings
from walkbike_routes import study as study_runs
from walkbike_routes.demo import Town
from walkbike_routes.download import geofabrik
from walkbike_routes.download.area import AreaError
from walkbike_routes.download.http import DownloadError
from walkbike_routes.download.lodes import CREDIT as LODES_CREDIT
from walkbike_routes.outputs.manifests import git_provenance
from walkbike_routes.report.map import build_maps
from walkbike_routes.report.tables import build_report

LAYERS = ("nodes", "links", "homes", "destinations", "access_points")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="walkbike-routes", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run a study and write its outputs and maps")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--out", type=Path, default=Path("output"))
    run.add_argument("--data", type=Path, help="where downloads are cached")
    run.add_argument("--no-map", action="store_true", help="skip the maps")
    run.add_argument(
        "--no-local",
        action="store_true",
        help="ignore the config's private .local.yaml layers (a public-data run)",
    )
    run.add_argument(
        "--refresh-osm",
        action="store_true",
        help="download OpenStreetMap again instead of using the saved extract",
    )
    report = commands.add_parser("report", help="write the maps and report from a finished run")
    report.add_argument("--out", type=Path, required=True, help="the run's output folder")
    for command in (run, report):
        command.add_argument(
            "--offline", action="store_true", help="build the map library into each map"
        )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # httpx logs full request URLs, keys included, at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.command == "report":
        if not (args.out / "manifest.json").exists():
            parser.exit(2, f"{args.out}: no manifest.json; is this a run's output folder?\n")
        _write_reports(args.out, maps=True, offline=args.offline)
        return
    try:
        study = settings.load(args.config, local=not args.no_local)
    except settings.ConfigError as err:
        parser.exit(2, f"{args.config}: {err}\n")
    if study.local_source is not None:
        logging.info("local layers: added from %s", study.local_source.name)
    if study.area.source != "demo":
        named = ", ".join(layer.name or layer.file.name for layer in study.local_layers)
        logging.info("local layers: %s", named or "none; OpenStreetMap only")
    try:
        written = build(study, args.out, args.data, refresh_osm=args.refresh_osm)
    except (settings.ConfigError, AreaError, DownloadError) as err:
        parser.exit(2, f"{args.config}: {err}\n")
    logging.info("wrote %s", written)
    _write_reports(args.out, maps=not args.no_map, offline=args.offline)


def _write_reports(out: Path, *, maps: bool, offline: bool) -> None:
    if not _has_destinations(out):
        logging.info("no destinations of the study's own: no maps or report")
        return
    paths = build_maps(out, offline=offline) if maps else []
    for path in [*paths, *build_report(out)]:
        logging.info("wrote %s", path)


def _has_destinations(out: Path) -> bool:
    """Whether a run routed to any destination of its own (network flows alone have none)."""
    try:
        return not pd.read_csv(out / "summary.csv").empty
    except pd.errors.EmptyDataError:
        return False


def build(
    study: settings.Study, out: Path, data: Path | None = None, *, refresh_osm: bool = False
) -> Path:
    """Write the study's layers to ``out`` and return the manifest's path.

    ``refresh_osm`` removes the saved OpenStreetMap extract first, so the
    streets are downloaded again.
    """
    if study.area.source == "demo":
        town = _demo_town(study)
        crs = study.crs or demo.CRS
    else:
        if study.synthetic:
            raise settings.ConfigError("a real area is not made up: remove synthetic: true")
        missing = settings.missing_files(study)
        if missing:  # before any download: a typo should not cost a long wait
            raise settings.ConfigError("; ".join(missing))
        cache = data or Path("data/raw") / _slug(study.study)
        if refresh_osm and study.area.osm_file is not None:
            raise settings.ConfigError(
                "--refresh-osm: area.osm_file names the streets to use; nothing is downloaded"
            )
        if refresh_osm:
            removed = geofabrik.forget(cache)
            logging.info("streets: removed %d saved OpenStreetMap file(s)", len(removed))
        town = layers.build(study, cache)
        crs = str(town.links.crs.to_string()) if town.links.crs else ""
    out.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    jobs = town.jobs.to_crs(crs) if town.jobs is not None else None
    town = dataclasses.replace(
        town, **{name: getattr(town, name).to_crs(crs) for name in LAYERS}, jobs=jobs
    )
    for name in LAYERS:
        layer: gpd.GeoDataFrame = getattr(town, name)
        path = out / f"{name}.parquet"
        layer.to_parquet(path)
        files[path.name] = _sha256(path)
    # An earlier run's outlines may be private: a run without them removes the file.
    drawn = layers.outlines(study, crs) if study.area.source != "demo" else None
    if drawn is not None and not drawn.empty:
        drawn.to_parquet(out / "outlines.parquet")
        files["outlines.parquet"] = _sha256(out / "outlines.parquet")
    else:
        (out / "outlines.parquet").unlink(missing_ok=True)
    if jobs is not None:
        jobs.to_parquet(out / "jobs.parquet")
        files["jobs.parquet"] = _sha256(out / "jobs.parquet")
    for path in [
        *study_runs.analyze(study, town, out),
        *network_flows.analyze(study, town, out),
    ]:
        files[str(path.relative_to(out))] = _sha256(path)
    manifest = {
        "study": study.study,
        "synthetic": study.synthetic,
        "written": datetime.now(UTC).isoformat(timespec="seconds"),
        "version": __version__,
        **_code(),
        "python": sys.version.split()[0],
        "packages": _packages(),
        "config": _sha256(study.source) if study.source else None,
        # The private .local.yaml beside the config, when the run used one.
        "local_config": _sha256(study.local_source) if study.local_source else None,
        "settings": _plain(study),
        "inputs": {
            where: {"file": path.name, "sha256": _sha256(path)}
            for where, path in settings.named_files(study)
            if path.is_file()  # a folder dataset is named in the settings but not hashed
        },
        "data": town.sources,
        "crs": crs,
        "sources": sources(study, town),
        "local_layers": [
            {"kind": layer.kind, "name": layer.name or layer.file.name}
            for layer in study.local_layers
        ],
        "exposure": study.exposure.as_dict(),
        "files": files,
    }
    path = out / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return path


def sources(study: settings.Study, town: Town) -> list[str]:
    """Credit lines for the data a run used, as maps and reports print them."""
    if study.area.source == "demo":
        return ["Made-up demo town (walkbike_routes.demo): no real streets, homes or people"]
    found = [
        "Streets: \u00a9 OpenStreetMap contributors (ODbL)",
        "Population: US Census Bureau, 2020 Census, Demographic and Housing Characteristics",
    ]
    if "building_source" in town.homes:
        buildings = sorted({str(s) for s in town.homes.building_source.dropna() if str(s)})
        if buildings:
            found.append(
                f"Home locations: {', '.join(buildings)} buildings, \u00a9 OpenStreetMap "
                "contributors, Overture Maps Foundation (ODbL)"
            )
    if "traffic_aadt_source" in town.links:
        counts = sorted({str(s) for s in town.links.traffic_aadt_source.dropna() if str(s)})
        if counts:
            found.append(f"Traffic counts: {', '.join(counts)} (each state DOT, via FHWA)")
    if town.jobs is not None:
        found.append(LODES_CREDIT)
    if "elevation_m" in town.nodes and town.nodes.elevation_m.notna().any():
        found.append("Elevation: USGS 3D Elevation Program")
    for layer in study.local_layers:
        found.append(f"Local {layer.kind}: {layer.name or layer.file.name}")
    for destination in study.destinations:
        if destination.outline is not None:
            drawn = destination.outline
            found.append(f"{drawn.name}, {destination.name}: {drawn.credit or drawn.file.name}")
        if destination.serves is not None:
            found.append(f"Homes counted, {destination.name}: {destination.serves.name}")
    return found


def _demo_town(study: settings.Study) -> Town:
    if not study.synthetic:
        raise settings.ConfigError("the demo area is made up: set synthetic: true")
    town = demo.build()
    if study.network_flows is not None:
        town = dataclasses.replace(town, jobs=demo.jobs())
    known = set(town.destinations.destination_id)
    unknown = [d.id for d in study.destinations if d.id not in known]
    if unknown:
        raise settings.ConfigError(
            f"destination {unknown[0]!r} is not in the demo town ({', '.join(sorted(known))})"
        )
    return town


def _code() -> dict[str, Any]:
    """The commit the code ran from, when it ran from a git checkout."""
    root = Path(__file__).resolve().parents[2]
    if not (root / ".git").exists():
        return {}
    found = git_provenance(root)
    return {"git_commit": found["git_commit"], "git_dirty": found["git_dirty"]}


def _packages() -> dict[str, str]:
    """The version of each package this one needs, as installed."""
    needed = {
        re.split(r"[\s;<>=!~\[(]", line, maxsplit=1)[0]
        for line in metadata.requires("walkbike-routes") or []
        if "extra ==" not in line
    }
    versions: dict[str, str] = {}
    for name in sorted(needed):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return versions


def _plain(value: Any) -> Any:
    """Settings as plain values for the manifest; a file is given by its name alone."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if isinstance(value, Path):
        return value.name
    return value


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "study"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    main()
