"""Route recorded trips under each cycling preset and write the route-choice report.

``python validation/run.py routes --config <study> --out <run> --traces
<file or cyclephilly>`` reads a finished run's streets (``links.parquet``,
``nodes.parquet``), routes every eligible trip under each of the study's
cycling presets (:mod:`wbvalidate.routes`) and writes
``<run>/validation/routes/``:

- ``trips.parquet``: every trip with its status, and for each preset the
  route status, modelled length and overlaps.  It holds the trip records:
  keep it private;
- ``streets.parquet``: each link any compared route used, with its count
  of ridden trips and of each preset's (:mod:`wbvalidate.makeup`).  Low
  counts can trace one rider's routes: keep it private too;
- ``report.md`` and ``report.html``: the scores, the routes' makeup, trips
  per street and breakdowns.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from walkbike_routes import config as settings
from walkbike_routes import study as study_runs
from walkbike_routes.costs import presets
from walkbike_routes.report.map import SYNTHETIC
from walkbike_routes.report.tables import (
    Block,
    Bullets,
    Heading,
    Para,
    Table,
    label,
    markdown,
    page,
    street_names,
)
from wbvalidate import makeup, routes, traces
from wbvalidate.routes import BUFFER_M, BUFFERS_M, ROUTED, RouteScore, column

log = logging.getLogger(__name__)

FOLDER = Path("validation") / "routes"
MIN_GROUP = 30  # trips, for a breakdown row


@dataclass(frozen=True)
class StreetChecks:
    """The makeup and trips-per-street results (:mod:`wbvalidate.makeup`)."""

    made_of: list[makeup.Makeup]
    scores: list[makeup.StreetScore]
    streets: pd.DataFrame  # trip-km per street and source


def check_routes(
    out: Path,
    study: settings.Study,
    recorded: gpd.GeoDataFrame,
    *,
    within: BaseGeometry | None,
    credit: str,
    buffers_m: tuple[float, ...] = BUFFERS_M,
) -> list[Path]:
    """Route the eligible trips under each cycling preset; write the outputs and return them."""
    names = list(study.presets.get("bike", [])) if "bike" in study.modes else []
    if not names:
        raise settings.ConfigError("the study has no cycling presets")
    manifest = read_manifest(out, study)
    links = gpd.read_parquet(out / "links.parquet")
    crs = str(links.crs)
    nodes = gpd.read_parquet(out / "nodes.parquet").to_crs(crs)
    trips = traces.check(recorded).to_crs(crs)
    status, lines = traces.eligible(trips, within)
    trips = trips.assign(status=status, length_m=lines.length)
    keep = status.eq(traces.USED)
    used = gpd.GeoDataFrame(trips.loc[keep, ["trip_id"]], geometry=lines[keep], crs=crs)
    log.info("routes: %d of %d trips eligible", len(used), len(trips))
    routed, edges = _route_all(study, links, nodes, used, names, out, buffers_m)
    table = pd.DataFrame(trips.drop(columns="geometry")).merge(
        routed.drop(columns="edges", errors="ignore"), on="trip_id", how="left"
    )
    folder = out / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    paths = [folder / "trips.parquet", folder / "report.md", folder / "report.html"]
    table.to_parquet(paths[0])
    checks, totals = street_checks(table, routed, used, edges, names)
    if not totals.empty:
        paths.insert(1, folder / "streets.parquet")
        lines = edges.set_index(edges.physical_edge_id.astype(str)).geometry
        gpd.GeoDataFrame(totals, geometry=lines.reindex(totals.index), crs=crs).to_parquet(paths[1])
    blocks = report_blocks(table, manifest, credit, names, buffers_m, checks=checks)
    paths[-2].write_text(markdown(blocks))
    paths[-1].write_text(page(blocks, _title(manifest)))
    return paths


def _route_all(
    study: settings.Study,
    links: gpd.GeoDataFrame,
    nodes: gpd.GeoDataFrame,
    used: gpd.GeoDataFrame,
    names: list[str],
    out: Path,
    buffers_m: tuple[float, ...],
) -> tuple[pd.DataFrame, gpd.GeoDataFrame]:
    """Route the eligible trips under each preset (none when no trip is eligible).

    Also returns the physical edges, with ``rideable`` true for those any
    preset's network rides on.
    """
    if used.empty:
        empty = pd.DataFrame(columns=["trip_id", "preset", "route_status", "edges"])
        return empty, gpd.GeoDataFrame(geometry=[], crs=links.crs)
    ends = routes.trip_ends(used.geometry, used.trip_id)
    context = routes.context(study, links, nodes, ends, out)
    frames, rideable = [], set()
    for name in names:
        preset = presets.load("bike", name)
        built = study_runs.mode_network(context, "bike", preset)
        rideable |= set(built.links.physical_edge_id.astype(str))
        frames.append(routes.route_trips(built, preset, used, context.edges, buffers_m))
        log.info("routes: routed under bike %s", name)
    edges = context.edges.assign(rideable=context.edges.physical_edge_id.astype(str).isin(rideable))
    return pd.concat(frames, ignore_index=True), edges


def street_checks(
    table: pd.DataFrame,
    routed: pd.DataFrame,
    used: gpd.GeoDataFrame,
    edges: gpd.GeoDataFrame,
    names: list[str],
) -> tuple[StreetChecks | None, pd.DataFrame]:
    """Route makeup and trips per street over the compared trips, and the trips per link."""
    rows = compared(table, names)
    if rows.empty or edges.empty:
        return None, pd.DataFrame()
    trip_ids = set(rows.trip_id.astype(str))
    rideable = edges[edges.rideable.astype(bool)]
    links = makeup.prepare_links(rideable, roads=edges)
    shapes = dict(zip(rideable.physical_edge_id.astype(str), rideable.geometry, strict=True))
    ridden = {
        str(t): g for t, g in zip(used.trip_id.astype(str), used.geometry, strict=True)
        if str(t) in trip_ids
    }  # fmt: skip
    lines = [(t, makeup.RIDDEN, g) for t, g in ridden.items()]
    done = routed[routed.trip_id.astype(str).isin(trip_ids)]
    for trip, name, found in zip(done.trip_id.astype(str), done.preset, done.edges, strict=True):
        line = makeup.oriented_line(tuple(found), shapes, Point(ridden[trip].coords[0]))
        if line is not None:
            lines.append((trip, str(name), line))
    described, links_used = makeup.collect(lines, links)
    user_of = rows.drop_duplicates("trip_id").set_index(rows.drop_duplicates("trip_id").trip_id
                                                        .astype(str)).user_id.astype(str)  # fmt: skip
    described = described.assign(user_id=described.trip_id.map(user_of))
    log.info("routes: matched %d routes to streets", len(described))
    totals = makeup.street_totals(links_used, links)
    length = pd.Series(links.length, index=links.ids)
    street = street_names(rideable.assign(link_id=rideable.physical_edge_id.astype(str)))
    checks = StreetChecks(
        made_of=makeup.makeup(described),
        scores=makeup.street_scores(links_used, user_of, baseline=routes.BASELINE),
        streets=makeup.street_table(totals, length, street),
    )
    return checks, totals


def read_manifest(out: Path, study: settings.Study) -> dict[str, Any]:
    """The run's manifest, or what the config says without one (a run stopped early)."""
    path = out / "manifest.json"
    if path.exists():
        found: dict[str, Any] = json.loads(path.read_text())
        return found
    return {"study": study.study, "synthetic": study.synthetic, "sources": []}


def compared(table: pd.DataFrame, names: list[str]) -> pd.DataFrame:
    """Rows of the eligible trips that every preset routed."""
    done = table[table.status.eq(traces.USED) & table.route_status.eq(ROUTED)]
    every = done.groupby("trip_id").preset.nunique().eq(len(names))
    return done[done.trip_id.isin(every[every].index)]


def _title(manifest: dict[str, Any]) -> str:
    study = str(manifest.get("study") or "")
    return (
        f"Modelled routes against recorded trips, {study}"
        if study
        else ("Modelled routes against recorded trips")
    )


def report_blocks(
    table: pd.DataFrame,
    manifest: dict[str, Any],
    credit: str,
    names: list[str],
    buffers_m: tuple[float, ...] = BUFFERS_M,
    *,
    checks: StreetChecks | None = None,
) -> list[Block]:
    blocks: list[Block] = [Heading(1, _title(manifest))]
    if manifest.get("synthetic"):
        blocks.append(Para(SYNTHETIC))
    blocks += [
        Para(
            "Each recorded trip is routed from its first point to its last under each cycling "
            "preset, and the modelled route is compared with the route ridden. Demand plays no "
            "part: this tests only which streets each preset chooses. Nothing was fitted to "
            "these trips."
        ),
        Heading(2, "Trips"),
        _status_table(table),
        _route_status_table(table, names),
    ]
    rows = compared(table, names)
    found = routes.route_scores(rows, buffer_m=BUFFER_M) if not rows.empty else []
    if not found:
        blocks.append(Para("No trip was routed under every preset: nothing to score.", note=True))
    else:
        blocks += [
            Heading(2, f"Scores, with a {BUFFER_M:g} m buffer"),
            _score_table(found),
            Heading(3, "Which way the routes differ"),
            _coverage_table(rows, names),
            Heading(3, "Buffer width"),
            _buffer_table(rows, names, buffers_m),
            *(_street_blocks(checks, names) if checks is not None else []),
            Heading(3, "By trip purpose"),
            _breakdown(rows, names, "purpose"),
            Heading(3, "By rider group"),
            _breakdown(rows, names, "group"),
        ]
    blocks += [
        Heading(2, "How to read this"),
        Bullets(
            (
                f"Overlap is the mean of two shares: of the trace within {BUFFER_M:g} m of the "
                "modelled route, and of the modelled route within that distance of the trace. "
                "It is 1 when the two routes are the same.",
                f"“Same route” is the share of trips with overlap of {routes.CLOSE:g} or more.",
                "Intervals are 95% bootstrap intervals over riders: riders are drawn with "
                "replacement, each with all their trips.",
                "“Better than shortest” is the preset's mean overlap minus the shortest-distance "
                "preset's, on the same trips and draws: an interval above 0 means the preset "
                "chooses riders' streets more often than plain shortest paths do.",
                "Trips are compared only where every preset found a route, so every row "
                "covers the same trips.",
                "Makeup and trips per street match ridden and modelled routes to street links "
                f"the same way: a link is used when at least {makeup.MATCH_SHARE:.0%} of it lies "
                f"within {BUFFER_M:g} m of the route. Stress levels are Furth's (LTS 1–4); "
                "“against a one-way” is riding a link the way bikes may not. Modelled routes "
                "obey one-way streets, so their share of it is matching noise (short one-way "
                "links beside the route): read the ridden share against theirs, not against 0.",
                "A busy-street crossing is where a primary, secondary or trunk street meets the "
                "route without being ridden along; turning onto it is not a crossing.",
                "Recorded trips come from people who chose to use a tracking app, often keen "
                "riders, and today's streets may differ from those when the trips were made.",
            )
        ),
        Heading(2, "Data"),
        Bullets((credit, *[str(s) for s in manifest.get("sources", [])])),
    ]
    return blocks


def _street_blocks(checks: StreetChecks, names: list[str]) -> list[Block]:
    blocks: list[Block] = [Heading(2, "What the routes are made of")]
    if checks.made_of:
        blocks += [
            Para(
                "Shares of each route's length matched to the network, and counts per km, "
                "summed over the compared trips. Ties between equally short routes in a grid "
                "matter little here: this asks whether a preset's routes are like riders' "
                "routes, not whether they are the same streets."
            ),
            _makeup_table(checks.made_of, values=True),
            Heading(3, "Difference from the ridden routes (95%)"),
            _makeup_table(checks.made_of, values=False),
        ]
    blocks.append(Heading(2, "Trips per street"))
    if checks.scores:
        blocks += [
            Para(
                "Each street link's count of trips whose ridden route uses it, ranked against "
                "the count whose modelled route does, over the links any route used. Summing "
                "many trips cancels the arbitrary choice between tied routes."
            ),
            _street_score_table(checks.scores),
        ]
    if not checks.streets.empty:
        blocks += [
            Heading(3, "Streets where the presets miss riders most (trip-km)"),
            _street_km_table(checks.streets, names),
        ]
    return blocks


def _makeup_table(found: list[makeup.Makeup], *, values: bool) -> Table:
    columns = [*makeup.SHARES, *makeup.RATES]
    out = []
    for item in found:
        cells = item.values if values else item.differences
        if not cells:
            continue
        row = [item.source]
        for column_, _ in columns:
            value, low, high = cells[column_]
            share = column_.endswith("_m")
            scale, unit, places = (100, "%", 0) if share else (1, "", 2)
            text = (
                f"{scale * value:.{places}f}{unit}"
                if values
                else _with_interval(scale * value, scale * low, scale * high, unit, places)
            )
            row.append(text if math.isfinite(value) else "–")
        out.append(tuple(row))
    return Table(
        ("Routes", *(name for _, name in columns)),
        tuple(out),
        (False, *(True for _ in columns)),
    )


def _street_score_table(found: list[makeup.StreetScore]) -> Table:
    rows = tuple(
        (
            s.preset,
            f"{s.links:,}",
            _with_interval(s.rho, s.low, s.high),
            _with_interval(s.difference, s.difference_low, s.difference_high),
        )
        for s in found
    )
    return Table(
        ("Preset", "Links", "Rank correlation with ridden trips (95%)",
         "Better than shortest (95%)"),
        rows,
        (False, True, True, True),
    )  # fmt: skip


def _street_km_table(streets: pd.DataFrame, names: list[str]) -> Table:
    columns = [makeup.RIDDEN, *(n for n in names if n in streets)]
    values = streets[columns].to_numpy(dtype=float)
    rows = tuple(
        (label(str(street)), *(f"{v:,.0f}" for v in row))
        for street, row in zip(streets.index, values, strict=True)
    )
    return Table(("Street", *columns), rows, (False, *(True for _ in columns)))


def _status_table(table: pd.DataFrame) -> Table:
    first = table.drop_duplicates("trip_id")
    counts = first.status.value_counts()
    rows = tuple((str(s), f"{int(n):,}") for s, n in counts.items())
    return Table(("Trip status", "Trips"), rows, (False, True))


def _route_status_table(table: pd.DataFrame, names: list[str]) -> Table:
    used = table[table.status.eq(traces.USED)]
    counts = pd.crosstab(used.route_status, used.preset).reindex(columns=names, fill_value=0)
    rows = tuple(
        (str(s), *(f"{int(v):,}" for v in values))
        for s, values in zip(counts.index, counts.to_numpy(), strict=True)
    )
    return Table(("Route status", *names), rows, (False, *(True for _ in names)))


def _score_table(found: list[RouteScore]) -> Table:
    rows = tuple(
        (
            s.preset,
            f"{s.trips:,}",
            f"{s.riders:,}",
            _with_interval(s.mean, s.low, s.high),
            _with_interval(s.difference, s.difference_low, s.difference_high),
            _with_interval(100 * s.close, 100 * s.close_low, 100 * s.close_high, "%", 0),
            _number(s.rider_mean),
        )
        for s in found
    )
    return Table(
        ("Preset", "Trips", "Riders", "Mean overlap (95%)", "Better than shortest (95%)",
         "Same route (95%)", "Mean over riders"),
        rows,
        (False, True, True, True, True, True, True),
    )  # fmt: skip


def _coverage_table(rows: pd.DataFrame, names: list[str]) -> Table:
    width = f"{BUFFER_M:g}m"
    out = []
    for name in names:
        part = rows[rows.preset.eq(name)]
        ratio = (part.route_m / part.length_m).median()
        out.append((name, _number(part[f"observed_{width}"].mean()),
                    _number(part[f"modelled_{width}"].mean()), _number(ratio)))  # fmt: skip
    return Table(
        ("Preset", "Trace covered by the route", "Route covered by the trace",
         "Route length ÷ trace length (median)"),
        tuple(out),
        (False, True, True, True),
    )  # fmt: skip


def _buffer_table(rows: pd.DataFrame, names: list[str], buffers_m: tuple[float, ...]) -> Table:
    out = [
        (name, *(_number(rows[rows.preset.eq(name)][column(b)].mean()) for b in buffers_m))
        for name in names
    ]
    return Table(
        ("Preset", *(f"{b:g} m" for b in buffers_m)),
        tuple(out),
        (False, *(True for _ in buffers_m)),
    )


def _breakdown(rows: pd.DataFrame, names: list[str], field: str) -> Table:
    labels = rows.drop_duplicates("trip_id")[field].fillna("").astype(str).replace("", "(none)")
    out = []
    for group, n in labels.value_counts().items():
        if n < MIN_GROUP:
            continue
        part = rows[rows[field].fillna("").astype(str).replace("", "(none)").eq(str(group))]
        means = [part[part.preset.eq(name)][column(BUFFER_M)].mean() for name in names]
        out.append((str(group), f"{int(n):,}", *(_number(m) for m in means)))
    if not out:
        out.append((f"(no group with {MIN_GROUP} or more trips)", "", *("" for _ in names)))
    return Table(("Group", "Trips", *names), tuple(out), (False, True, *(True for _ in names)))


def _number(value: float) -> str:
    return "–" if not np.isfinite(value) else f"{value:.2f}"


def _with_interval(value: float, low: float, high: float, unit: str = "", places: int = 2) -> str:
    if not math.isfinite(value):
        return "–"
    text = f"{value:.{places}f}{unit}"
    if math.isfinite(low) and math.isfinite(high):
        text += f" ({low:.{places}f} to {high:.{places}f}{unit})"
    return text
