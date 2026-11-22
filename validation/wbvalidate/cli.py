"""Check walkbike-routes against observed walking and cycling.

Kept apart from the package while it is an experiment: it imports
walkbike_routes as a library and adds nothing to it.  From the repository
root:

    uv run --locked python validation/run.py counts --out <run> --counts dvrpc \\
        --within validation/config/philadelphia_core.geojson
    uv run --locked python validation/run.py routes \\
        --config validation/config/philadelphia_core.yaml --out <run> --traces cyclephilly

``counts`` compares a run's network flows with counts
(:mod:`wbvalidate.report`); ``routes`` compares each cycling preset's routes
with recorded trips (:mod:`wbvalidate.route_report`).  See validation/README.md.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from pathlib import Path

import geopandas as gpd

from walkbike_routes import config as settings
from walkbike_routes import layers
from walkbike_routes.download.http import DownloadError, Fetcher
from walkbike_routes.exceptions import AccessibilityError
from wbvalidate import counts as count_files
from wbvalidate import cyclephilly, dvrpc
from wbvalidate import traces as trace_files
from wbvalidate.report import validate
from wbvalidate.route_report import check_routes


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="validation/run.py", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("counts", help="compare a run's network flows with counts")
    check.add_argument("--out", type=Path, required=True, help="a run with network_flows")
    check.add_argument(
        "--counts", required=True, help="a counts file, or 'dvrpc' for the Philadelphia region's"
    )
    check.add_argument("--within", type=Path, help="compare only sites inside this outline")
    check.add_argument("--data", type=Path, help="where downloads are cached")
    trips = commands.add_parser(
        "routes", help="compare each cycling preset's routes with recorded trips"
    )
    trips.add_argument("--config", type=Path, required=True, help="the run's study config")
    trips.add_argument("--out", type=Path, required=True, help="the run's output folder")
    trips.add_argument(
        "--traces", required=True, help="a traces file, or 'cyclephilly' for Philadelphia's"
    )
    trips.add_argument(
        "--within",
        type=Path,
        help="compare only trips inside this outline (default: the study's boundary file)",
    )
    trips.add_argument("--data", type=Path, help="where downloads are cached")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    needed = "manifest.json" if args.command == "counts" else "links.parquet"
    if not (args.out / needed).exists():
        parser.exit(2, f"{args.out}: no {needed}; is this a run's output folder?\n")
    try:
        if args.command == "counts":
            paths = _counts(args.out, args.counts, args.within, args.data)
        else:
            paths = _routes(args.config, args.out, args.traces, args.within, args.data)
    except (AccessibilityError, DownloadError, settings.ConfigError) as err:
        parser.exit(2, f"{args.command}: {err}\n")
    for path in paths:
        logging.info("wrote %s", path)


def _counts(out: Path, counts: str, within: Path | None, data: Path | None) -> list[Path]:
    """Read the counts (a file, or DVRPC's) and write the run's validation report."""
    links_crs = str(json.loads((out / "manifest.json").read_text())["crs"])
    if counts == "dvrpc":
        found = dvrpc.counts(Fetcher(data or Path("data/raw/dvrpc")))
        credit = dvrpc.CREDIT
    else:
        found = count_files.read(Path(counts))
        names = sorted({s for s in found.source if s})
        credit = f"Counts: {', '.join(names) if names else Path(counts).name}"
    area = layers.outline(within, links_crs) if within is not None else None
    return validate(out, found, within=area, credit=credit)


def _routes(
    config: Path, out: Path, traces: str, within: Path | None, data: Path | None
) -> list[Path]:
    """Read the trips (a file, or CyclePhilly's) and write the run's route-choice report."""
    study = settings.load(config)
    crs = str(gpd.read_parquet(out / "links.parquet", columns=["geometry"]).crs)
    if traces == "cyclephilly":
        found = cyclephilly.trips(Fetcher(data or Path("data/raw/cyclephilly")))
        credit = cyclephilly.CREDIT
    else:
        found = trace_files.read(Path(traces))
        credit = f"Recorded trips: {Path(traces).name}"
    outline = within or study.area.boundary_file
    area = layers.outline(outline, crs) if outline is not None else None
    return check_routes(out, study, found, within=area, credit=credit)
