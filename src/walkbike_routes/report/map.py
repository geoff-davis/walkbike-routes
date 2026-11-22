"""A map of a study run: one page per destination, ``out/<destination>/map.html``.

The page shows each run's potential flow (line width), where homes can reach
the destination from (grid squares shaded by travel time), the streets that
are busy under every route-choice preset of a mode and those busy under only
some, the improvement screen's top sections, and where car and walk or bike
flows overlap.  When the study wrote lists for all its destinations
together, ``out/all-destinations/map.html`` shows those, over every
destination's trips added up.  A switcher picks the run; "Print this view" prints the map at
its current zoom with the key and a summary.

It is a single HTML file with the study's own streets drawn as a background,
so it opens from disk.  ``offline`` also builds the map library (Leaflet,
``report/vendor``) into the page, leaving no link to the internet.
"""

from __future__ import annotations

import html
import json
import math
import re
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, cast

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import Point, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from walkbike_routes.analysis.exposure import ALL_DESTINATIONS
from walkbike_routes.analysis.exposure_rules import (
    ExposureRules,
    rules_from,
)
from walkbike_routes.analysis.screens import PARTS_PER_STREET
from walkbike_routes.costs import presets
from walkbike_routes.report import text
from walkbike_routes.report.text import (
    DRAFT,
    FALLBACK_SOURCES,
    NOT_COUNTS,
    SYNTHETIC,
    from_each,
)

WGS84 = "EPSG:4326"
CELL_M = 150.0  # catchment grid squares: coarse enough not to show single buildings
TOP_SHARE = 0.10  # "busy": the top tenth of a run's streets with flow
SCREEN_ROWS = 10
OPENING_FLOW_SHARE = 0.05  # the opening view frames streets with this share of the top flow
MARGIN_M = 400.0  # background streets drawn this far beyond the flows and homes
STREET_CLASSES = {  # basemap line classes, most important first
    "M": ("motorway", "trunk", "primary", "secondary"),
    "T": ("tertiary",),
    "L": ("residential", "unclassified", "living_street"),
    "S": ("service",),
}
# Characters that could end a <script> block or open an HTML comment inside one,
# and the two line separators JavaScript treats as line ends, as JSON escapes.
SCRIPT_SAFE = {
    "<": "\\u003c",
    ">": "\\u003e",
    "&": "\\u0026",
    " ": "\\u2028",
    " ": "\\u2029",
}
LEAFLET_CSS = '<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">'
LEAFLET_JS = (
    '<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>'
)
# The OpenStreetMap tile layer, offered only on the online page.
TILES = 'const TILES="https://tile.openstreetmap.org/{z}/{x}/{y}.png";'
NO_TILES = "const TILES=null;"


@dataclass(frozen=True)
class Run:
    """One mode and preset's outputs for a destination."""

    mode: str
    preset: str
    folder: Path
    summary: dict[str, Any]
    main: bool

    @property
    def id(self) -> str:
        return f"{self.mode}-{self.preset}"


def build_maps(out: Path, *, offline: bool = False) -> list[Path]:
    """Write ``map.html`` for each destination in a run folder; return the paths."""
    manifest = json.loads((out / "manifest.json").read_text())
    # Identifiers stay text: a destination "001" is not the number 1.
    # (Nor is "NA" missing.)
    ids = ["destination_id", "mode", "preset"]
    summary = pd.read_csv(out / "summary.csv")
    summary[ids] = pd.read_csv(out / "summary.csv", usecols=ids, dtype=str, keep_default_na=False)[
        ids
    ]
    summary = in_order(summary)
    links = gpd.read_parquet(out / "links.parquet")
    homes = gpd.read_parquet(out / "homes.parquet")
    destinations = gpd.read_parquet(out / "destinations.parquet")
    # Only files this run wrote: a rerun into the same folder leaves old ones.
    listed = {out / name for name in manifest["files"]}
    outlines = (
        gpd.read_parquet(out / "outlines.parquet") if out / "outlines.parquet" in listed else None
    )
    written = []
    for destination_id in dict.fromkeys(summary.destination_id.astype(str)):
        rows = summary[summary.destination_id.astype(str).eq(destination_id)]
        runs, seen = [], set()
        for row in rows.to_dict("records"):
            mode, preset = str(row["mode"]), str(row["preset"])
            runs.append(
                Run(mode, preset, out / destination_id / f"{mode}-{preset}",
                    _clean({str(k): v for k, v in row.items()}), main=mode not in seen)
            )  # fmt: skip
            seen.add(mode)
        target = destinations[destinations.destination_id.astype(str).eq(destination_id)]
        drawn = (
            None
            if outlines is None
            else outlines[outlines.destination_id.astype(str).eq(destination_id)]
        )
        data = page_data(
            destination_id, runs, links, homes, target, out / destination_id, listed, drawn
        )
        text = page(data, manifest, offline=offline)
        path = out / destination_id / "map.html"
        path.write_text(text)
        written.append(path)
    together = combined_page_data(out, summary, links, destinations, listed, outlines)
    if together is not None:
        path = out / ALL_DESTINATIONS / "map.html"
        path.write_text(page(together, manifest, offline=offline))
        written.append(path)
    return written


def in_order(summary: pd.DataFrame) -> pd.DataFrame:
    """The summary with each destination's modes as walking, cycling, driving.

    Destinations keep their order, and so do a mode's presets: its first is its main one.
    """
    place = {d: i for i, d in enumerate(dict.fromkeys(summary.destination_id))}
    keyed = summary.assign(
        _place=summary.destination_id.map(place), _mode=summary["mode"].map(text.mode_rank)
    )
    return keyed.sort_values(["_place", "_mode"], kind="stable").drop(columns=["_place", "_mode"])


def _flow_words(unit: object, curve: object) -> dict[str, Any]:
    """Who a run's flows count, as the page's headings and sentences say it."""
    words, is_decayed = text.unit_words(unit), text.decayed(curve)
    return {
        "unit": words,
        "flow_heading": text.flow_heading(words, is_decayed),
        "flow_words": text.flow_words(words, is_decayed),
    }


def combined_page_data(
    out: Path,
    summary: pd.DataFrame,
    links: gpd.GeoDataFrame,
    destinations: gpd.GeoDataFrame,
    listed: set[Path],
    outlines: gpd.GeoDataFrame | None = None,
) -> dict[str, Any] | None:
    """The page for every destination together, or None when the run has no such lists.

    Each mode's line widths are the destinations' main-run flows added up; the
    places are the all-destinations exposure lists, each saying how many of
    its people come from each destination.
    """
    folder = out / ALL_DESTINATIONS
    folders = sorted(
        {p.parent for p in listed if p.parent.parent == folder},
        key=lambda run: (text.mode_rank(run.name.partition("-")[0]), run.name),
    )
    # A destination of that name holds its own runs there, and the run writes no lists.
    if not folders or summary.destination_id.astype(str).eq(ALL_DESTINATIONS).any():
        return None
    to_degrees = Transformer.from_crs(links.crs, WGS84, always_xy=True)
    ids = list(dict.fromkeys(summary.destination_id.astype(str)))
    names = destinations.get("name", destinations.destination_id).astype(str)
    labels = dict(zip(destinations.destination_id.astype(str), names, strict=True))
    shapes: dict[str, BaseGeometry] = {}
    totals: dict[str, dict[str, float]] = {}
    for run in folders:
        total: dict[str, float] = defaultdict(float)
        for destination_id in ids:
            path = out / destination_id / run.name / "flows.parquet"
            if path not in listed:
                continue
            frame = gpd.read_parquet(path)
            used = frame[frame.potential_flow.gt(0)]
            for edge_id, flow, line in zip(used.physical_edge_id.astype(str),
                                           used.potential_flow, used.geometry, strict=True):  # fmt: skip
                total[edge_id] += float(flow)
                shapes.setdefault(edge_id, line)
        totals[run.name] = total
    order = sorted(shapes)
    position = {edge_id: i for i, edge_id in enumerate(order)}
    street_names = dict(
        zip(links.link_id.astype(str), links.name.fillna("").astype(str), strict=True)
    )
    run_data = []
    for run in folders:
        mode, _, preset = run.name.partition("-")
        rows = summary[summary["mode"].astype(str).eq(mode) & summary.preset.astype(str).eq(preset)]
        units = set(rows.weight_unit.astype(str)) if "weight_unit" in rows else set()
        curves = rows.get("decay_curve", pd.Series(dtype=str))
        every_decayed = bool(len(curves)) and bool(curves.map(text.decayed).all())
        words = _flow_words(units.pop() if len(units) == 1 else "", "all" if every_decayed else "")
        title, source = _preset(mode, preset)
        run_data.append(
            {
                "id": run.name, "mode": mode, "preset": preset, "main": True, "title": title,
                "source": source,
                "summary": {"weight_unit": words["unit"]}, **words,
                "flow": [[position[e], round(f, 2)] for e, f in sorted(totals[run.name].items())],
                "cells": [], "screen": [],
                **_exposure(run, to_degrees, lambda path: path in listed, labels),
            }
        )  # fmt: skip
    shown = destinations[destinations.destination_id.astype(str).isin(ids)]
    places = unary_union(list(shown.geometry))
    drawn = gpd.GeoSeries([shapes[e] for e in order], crs=links.crs) if order else links.geometry
    first = totals[folders[0].name]
    flow = gpd.GeoDataFrame(
        {"potential_flow": [first.get(e, 0.0) for e in order]},
        geometry=[shapes[e] for e in order], crs=links.crs,
    )  # fmt: skip
    west, south, east, north = opening_view(flow, None, links.iloc[:0], places, drawn)
    box_ = box(*(drawn.total_bounds + np.array([-MARGIN_M, -MARGIN_M, MARGIN_M, MARGIN_M])))
    lines, street_labels = basemap(links[links.intersects(box_)], to_degrees)
    w, s_ = to_degrees.transform(west, south)
    e, n = to_degrees.transform(east, north)
    kept = (
        outlines[outlines.destination_id.astype(str).isin(ids)].drop_duplicates("name")
        if outlines is not None
        else None
    )
    return {
        "id": ALL_DESTINATIONS,
        "name": "All destinations",
        "combined": [labels.get(d, d) for d in ids],
        "outline": _outline(shown.geometry.iloc[0], to_degrees),
        # Each shape with its own name, whatever order the two tables list them in.
        "places": [
            {**_outline(shape, to_degrees), "name": labels.get(d, d)}
            for d, shape in zip(shown.destination_id.astype(str), shown.geometry, strict=True)
        ],
        "outlines": [
            {"name": str(name), "served": bool(served), "rings": _rings(shape, to_degrees)}
            for name, served, shape in (
                [] if kept is None else zip(kept.name, kept.served, kept.geometry, strict=True)
            )
        ],
        "entrances": _entrances(
            sorted({out / d / r.name for d in ids for r in folders}),
            to_degrees,
            lambda path: path in listed,
        ),  # fmt: skip
        "edges": {
            "g": [_line(shapes[e], to_degrees) for e in order],
            "n": [street_names.get(e, "") for e in order],
        },
        "runs": run_data,
        "agreement": {},
        "overlap": [],
        "cells": [],
        "bounds": [[round(s_, 5), round(w, 5)], [round(n, 5), round(e, 5)]],
        "streets": lines,
        "labels": street_labels,
    }


def page_data(
    destination_id: str,
    runs: list[Run],
    links: gpd.GeoDataFrame,
    homes: gpd.GeoDataFrame,
    target: gpd.GeoDataFrame,
    folder: Path,
    listed: set[Path] | None = None,
    shown: gpd.GeoDataFrame | None = None,
) -> dict[str, Any]:
    """Everything the page draws, in latitude and longitude.

    ``shown`` holds the destination's outlines, such as its attendance area.
    """
    to_degrees = Transformer.from_crs(links.crs, WGS84, always_xy=True)

    def found(path: Path) -> bool:
        return path in listed if listed is not None else path.exists()

    flows = {run.id: gpd.read_parquet(run.folder / "flows.parquet") for run in runs}
    overlap_path = folder / "overlap.parquet"
    overlap = gpd.read_parquet(overlap_path) if found(overlap_path) else None

    # Streets drawn: any with flow in a run, or in an overlap tier.  Each is stored
    # once and runs refer to it by position.
    shapes: dict[str, BaseGeometry] = {}
    for frame in flows.values():
        used = frame[frame.potential_flow.gt(0)]
        for edge_id, line in zip(used.physical_edge_id.astype(str), used.geometry, strict=True):
            shapes.setdefault(edge_id, line)
    tiers: dict[str, str] = {}
    if overlap is not None:
        tiered = overlap[overlap.overlap_tier.fillna("").astype(str).ne("")]
        for edge_id, line, tier in zip(
            tiered.physical_edge_id.astype(str), tiered.geometry, tiered.overlap_tier, strict=True
        ):
            shapes.setdefault(edge_id, line)
            tiers[edge_id] = str(tier)
    order = sorted(shapes)
    position = {edge_id: i for i, edge_id in enumerate(order)}
    names = dict(zip(links.link_id.astype(str), links.name.fillna("").astype(str), strict=True))
    edges = {
        "g": [_line(shapes[e], to_degrees) for e in order],
        "n": [names.get(e, "") for e in order],
    }

    routes = {run.id: pd.read_parquet(run.folder / "routes.parquet") for run in runs}
    routed = set().union(*(set(r.origin_id.astype(str)) for r in routes.values()))
    homes = homes[homes.home_id.astype(str).isin(routed)]
    cells, cell_index = _cells(homes, to_degrees)
    run_data = []
    for run in runs:
        frame = flows[run.id]
        used = frame[frame.potential_flow.gt(0)]
        preset = _preset(run.mode, run.preset)
        run_data.append(
            {
                "id": run.id,
                "mode": run.mode,
                "preset": run.preset,
                "main": run.main,
                "title": preset[0],
                "source": preset[1],
                "summary": run.summary,
                **_flow_words(run.summary.get("weight_unit"), run.summary.get("decay_curve")),
                "flow": [
                    [position[str(e)], round(float(f), 2)]
                    for e, f in zip(used.physical_edge_id, used.potential_flow, strict=True)
                ],
                "cells": catchment(routes[run.id], cell_index),
                "screen": _screen(run.folder / "screen.parquet", to_degrees, found),
                **_exposure(run.folder, to_degrees, found),
            }
        )
    agreement = {
        mode: {tier: [position[e] for e in sorted(ids)] for tier, ids in found.items()}
        for mode, found in busy_agreement(
            {run.id: (run.mode, flows[run.id]) for run in runs}
        ).items()
    }
    shape = target.geometry.iloc[0]
    entrances = _entrances([run.folder for run in runs], to_degrees, found)
    drawn = gpd.GeoSeries([shapes[e] for e in order], crs=links.crs) if order else links.geometry
    west, south, east, north = opening_view(
        flows[runs[0].id] if runs else None,
        routes[runs[0].id] if runs else None,
        homes,
        shape,
        drawn,
    )
    # The background streets: those near what is drawn, flows or homes.
    reach = [drawn.total_bounds] + ([homes.total_bounds] if not homes.empty else [])
    box_ = box(
        min(b[0] for b in reach) - MARGIN_M, min(b[1] for b in reach) - MARGIN_M,
        max(b[2] for b in reach) + MARGIN_M, max(b[3] for b in reach) + MARGIN_M,
    )  # fmt: skip
    w, s_ = to_degrees.transform(west, south)
    e, n = to_degrees.transform(east, north)
    lines, labels = basemap(links[links.intersects(box_)], to_degrees)
    return {
        "id": destination_id,
        "name": str(target.name.iloc[0]) if "name" in target else destination_id,
        "outline": _outline(shape, to_degrees),
        "outlines": [
            {
                "name": str(name),
                "served": bool(served),
                "rings": _rings(shape, to_degrees),
            }
            for name, served, shape in (
                []
                if shown is None
                else zip(shown.name, shown.served, shown.geometry, strict=True)  # in the run's CRS
            )
        ],
        "entrances": entrances,
        "edges": edges,
        "runs": run_data,
        "agreement": agreement,
        "overlap": [[position[e], t] for e, t in sorted(tiers.items())],
        "cells": cells,
        "bounds": [[round(s_, 5), round(w, 5)], [round(n, 5), round(e, 5)]],
        "streets": lines,
        "labels": labels,
    }


def opening_view(
    flow: gpd.GeoDataFrame | None,
    routes: pd.DataFrame | None,
    homes: gpd.GeoDataFrame,
    destination: BaseGeometry,
    drawn: gpd.GeoSeries,
) -> tuple[float, float, float, float]:
    """Where the page opens, for the run shown first: west, south, east, north.

    It frames the destination, the homes within the time limit (the shaded
    squares) and the streets carrying at least a twentieth of the busiest
    street's flow.  Faint flows from far-off homes are left outside, or
    the shaded area would open as a speck.  With none of those, every
    street drawn is framed.
    """
    parts: list[BaseGeometry] = [destination]
    if flow is not None and not flow.empty and float(flow.potential_flow.max()) > 0:
        top = float(flow.potential_flow.max())
        parts.extend(flow.geometry[flow.potential_flow.ge(top * OPENING_FLOW_SHARE)])
    if routes is not None and "within_time_limit" in routes:
        # Car runs hold both directions; the squares shade the trip there, so this does too.
        if "direction" in routes and routes.direction.eq("to_destination").any():
            routes = routes[routes.direction.eq("to_destination")]
        within = routes.within_time_limit.fillna(False).astype(bool)
        near = set(routes.origin_id[within].astype(str))
        parts.extend(homes.geometry[homes.home_id.astype(str).isin(near)])
    framed = gpd.GeoSeries(parts, crs=drawn.crs)
    width, height = (framed.total_bounds[2:] - framed.total_bounds[:2]).tolist()
    if len(parts) == 1 or (width < 1 and height < 1):
        framed = drawn
    west, south, east, north = (float(v) for v in framed.total_bounds)
    return west, south, east, north


def busy_agreement(runs: dict[str, tuple[str, pd.DataFrame]]) -> dict[str, dict[str, set[str]]]:
    """For each mode run under two or more presets, its busy streets by agreement.

    A street is busy in a run when its flow is in the top ``TOP_SHARE`` of the
    streets with any flow there.  ``every`` holds the streets busy in every
    run of the mode, ``some`` those busy in at least one but not all.
    """
    by_mode: dict[str, list[set[str]]] = {}
    for mode, frame in runs.values():
        flow = frame.set_index(frame.physical_edge_id.astype(str)).potential_flow
        flow = flow[flow.gt(0)]
        busy: set[str] = set()
        if not flow.empty:
            threshold = float(np.quantile(flow.to_numpy(), 1 - TOP_SHARE))
            busy = set(flow.index[flow.ge(threshold)])
        by_mode.setdefault(mode, []).append(busy)
    result = {}
    for mode, sets in by_mode.items():
        if len(sets) < 2:
            continue
        every = set.intersection(*sets)
        result[mode] = {"every": every, "some": set.union(*sets) - every}
    return result


def catchment(routes: pd.DataFrame, cell_index: pd.Series) -> list[list[Any]]:
    """Each grid square's weight and weighted median travel time, in minutes.

    ``cell_index`` maps a home to its square.  Car runs count the trip to the
    destination.  A home with no route counts as never arriving, so a square
    whose median home cannot get there has ``None``.  Squares with no weight
    are left out.  Rows are ``[square, minutes or None, weight]``.
    """
    if "direction" in routes and routes.direction.eq("to_destination").any():
        routes = routes[routes.direction.eq("to_destination")]
    minutes = (routes.total_travel_time_s / 60.0).where(routes.route_reachable.astype(bool))
    frame = pd.DataFrame(
        {
            "cell": routes.origin_id.astype(str).map(cell_index),
            "minutes": minutes.fillna(math.inf).to_numpy(),
            "weight": routes.demand_weight.fillna(0).to_numpy(dtype=float),
        }
    ).dropna(subset=["cell"])
    rows = []
    for cell, group in frame[frame.weight.gt(0)].groupby("cell", sort=True):
        group = group.sort_values("minutes", kind="stable")
        total = float(group.weight.sum())
        middle = float(group.minutes.iloc[int(np.searchsorted(group.weight.cumsum(), total / 2))])
        rows.append(
            [int(str(cell)), round(middle, 3) if math.isfinite(middle) else None, round(total, 1)]
        )
    return rows


def basemap(
    links: gpd.GeoDataFrame, to_degrees: Transformer
) -> tuple[dict[str, list[Any]], list[list[Any]]]:
    """Street lines by class and street-name labels, in latitude and longitude."""
    highway = links.highway.fillna("").astype(str)
    kind = pd.Series("P", index=links.index)
    for key, classes in reversed(list(STREET_CLASSES.items())):
        kind[highway.isin(classes)] = key
    lines: dict[str, list[Any]] = {key: [] for key in "MTLSP"}
    labels: dict[tuple[str, int, int], tuple[float, list[Any]]] = {}
    names = links.name.fillna("").astype(str)
    for line, name, key in zip(links.geometry, names, kind, strict=True):
        lines[key].append(_line(line, to_degrees))
        if not (name and key != "P" and line.length > 40):
            continue
        middle = line.interpolate(0.5, normalized=True)
        cell = (name, int(middle.x // 300), int(middle.y // 300))
        if cell in labels and line.length <= labels[cell][0]:
            continue
        a, b = line.interpolate(0.4, normalized=True), line.interpolate(0.6, normalized=True)
        angle = float(np.degrees(np.arctan2(b.x - a.x, b.y - a.y))) % 180 - 90
        lon, lat = to_degrees.transform(middle.x, middle.y)
        labels[cell] = (line.length, [name, round(lat, 5), round(lon, 5), round(angle, 1), key])
    return lines, [entry for _, entry in labels.values()]


def page(data: dict[str, Any], manifest: dict[str, Any], *, offline: bool = False) -> str:
    """Fill the page template with one destination's data."""
    template = resources.files("walkbike_routes.report").joinpath("map.html").read_text()
    credits = [str(s) for s in manifest.get("sources", FALLBACK_SOURCES)]
    warn = " ".join([SYNTHETIC, DRAFT] if manifest.get("synthetic") else [DRAFT])
    rules = run_rules(manifest)
    fills = {
        "/*TITLE*/": html.escape(_title(str(data["name"]), str(manifest.get("study", "")))),
        "/*MODES*/": compact(text.MODE_NAMES),
        "/*WARN*/": html.escape(warn),
        "/*NOTCOUNTS*/": html.escape(NOT_COUNTS),
        "/*CREDITS*/": html.escape("; ".join(credits)),
        "/*DATA*/": compact(
            {
                **data,
                "exposure_note": text.exposure_note(rules),
                # With the plain rule a fix-first place's people and points are its whole reason.
                "plain_rule": text.standard_reason(rules.fix_first),
            }
        ),
        "/*CREDITLIST*/": compact(credits),
        "/*TILES*/": NO_TILES if offline else TILES,
    }
    # One pass, so a mark inside a street name is not filled in turn.
    filled = re.sub("|".join(map(re.escape, fills)), lambda m: fills[m.group(0)], template)
    return inline_leaflet(filled) if offline else filled


def run_rules(manifest: Mapping[str, Any]) -> ExposureRules:
    """The exposure rules a run used; a run from before they were recorded used the defaults."""
    return rules_from(manifest.get("exposure"))


def compact(value: Any) -> str:
    """JSON for inside a <script> block: no text can close it or open a comment."""
    text = json.dumps(value, separators=(",", ":"), allow_nan=False)
    for mark, safe in SCRIPT_SAFE.items():
        text = text.replace(mark, safe)
    return text


def inline_leaflet(html_text: str) -> str:
    """Replace the page's links to Leaflet with the copy in ``report/vendor``."""
    vendor = resources.files("walkbike_routes.report").joinpath("vendor")
    css = vendor.joinpath("leaflet.css").read_text()
    js = vendor.joinpath("leaflet.js").read_text()
    for tag in (LEAFLET_CSS, LEAFLET_JS):
        if html_text.count(tag) != 1:
            raise ValueError(f"the map template does not link Leaflet as expected: {tag}")
    if "</style" in css.lower() or "</script" in js.lower():
        raise ValueError("the vendored Leaflet would close its own block")
    html_text = html_text.replace(LEAFLET_CSS, f"<style>{css}</style>", 1)
    return html_text.replace(LEAFLET_JS, f"<script>{js}</script>", 1)


def _cells(homes: gpd.GeoDataFrame, to_degrees: Transformer) -> tuple[list[list[float]], pd.Series]:
    """Grid squares holding homes, as [south, west, north, east], and each home's square."""
    x = np.floor(homes.geometry.x.to_numpy() / CELL_M).astype(np.int64)
    y = np.floor(homes.geometry.y.to_numpy() / CELL_M).astype(np.int64)
    keys = sorted(set(zip(x.tolist(), y.tolist(), strict=True)))
    number = {key: i for i, key in enumerate(keys)}
    squares = []
    for cx, cy in keys:
        west, south = to_degrees.transform(cx * CELL_M, cy * CELL_M)
        east, north = to_degrees.transform((cx + 1) * CELL_M, (cy + 1) * CELL_M)
        squares.append([round(south, 6), round(west, 6), round(north, 6), round(east, 6)])
    index = pd.Series(
        [number[key] for key in zip(x.tolist(), y.tolist(), strict=True)],
        index=homes.home_id.astype(str).to_numpy(),
    )
    return squares, index


def _line(line: BaseGeometry, to_degrees: Transformer) -> list[list[float]]:
    xy = np.asarray(line.simplify(1.5).coords)
    lon, lat = to_degrees.transform(xy[:, 0], xy[:, 1])
    return [[round(float(a), 5), round(float(b), 5)] for a, b in zip(lat, lon, strict=True)]


def _outline(shape: BaseGeometry, to_degrees: Transformer) -> dict[str, Any]:
    if shape.geom_type == "Point":
        point = cast(Point, shape)
        lon, lat = to_degrees.transform(point.x, point.y)
        return {"point": [round(lat, 5), round(lon, 5)]}
    parts = getattr(shape, "geoms", [shape])
    rings = [_line(part.exterior, to_degrees) for part in parts if hasattr(part, "exterior")]
    return {"rings": rings}


def _rings(shape: BaseGeometry, to_degrees: Transformer) -> list[list[list[float]]]:
    """Every boundary of a drawn outline, holes included: an enclave left out shows."""
    rings = []
    for part in getattr(shape, "geoms", [shape]):
        if hasattr(part, "exterior"):
            rings.append(_line(part.exterior, to_degrees))
            rings.extend(_line(hole, to_degrees) for hole in part.interiors)
    return rings


def _entrances(
    folders: list[Path], to_degrees: Transformer, found: Callable[[Path], bool]
) -> list[list[float]]:
    points: set[tuple[float, float]] = set()
    for folder in folders:
        path = folder / "entrances.parquet"
        if not found(path):
            continue
        for point in cast(list[Point], list(gpd.read_parquet(path).geometry)):
            lon, lat = to_degrees.transform(point.x, point.y)
            points.add((round(lat, 5), round(lon, 5)))
    return [list(p) for p in sorted(points)]


def _screen(
    path: Path, to_degrees: Transformer, found: Callable[[Path], bool]
) -> list[dict[str, Any]]:
    if not found(path):
        return []
    read = gpd.read_parquet(path)
    order = list(dict.fromkeys(read.treatment.astype(str)))  # as the run wrote them
    ranked = read.assign(_order=read.treatment.astype(str).map(order.index))
    top = ranked.sort_values(["_order", "rank"]).groupby("_order").head(SCREEN_ROWS)
    rows = []
    for row in top.to_dict("records"):
        geometry = row["geometry"]
        parts = list(getattr(geometry, "geoms", [geometry]))
        points = [p for p in parts if p.geom_type == "Point"]
        rows.append(
            {
                "rank": int(row["rank"]),
                "label": str(row["label"]),
                "treatment": str(row["treatment"]),
                "minutes": round(float(row["weighted_generalized_minutes_saved"]), 1),
                "helped": _finite(row["benefiting_weight"], 1),
                "counted": _finite(row["analyzed_weight"], 1),
                # As the report prints them: part of one person is not "0".
                "helped_text": text.people(_finite(row["benefiting_weight"], 12)),
                "counted_text": text.people(_finite(row["analyzed_weight"], 12)),
                "share": _share(row["benefiting_weight"], row["analyzed_weight"]),
                "part": _part_note(row),
                "lines": [_line(p, to_degrees) for p in parts if p.geom_type != "Point"],
                "points": [_line(p, to_degrees)[0] for p in points],
            }
        )
    return rows


EXPOSURE_ROWS = 10


def _exposure(
    folder: Path,
    to_degrees: Transformer,
    found: Callable[[Path], bool],
    labels: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """A run's streets and crossings for the page, and how many are fix first.

    Every fix-first place is drawn, as the report lists every one, and the
    rest of each list's top places.  With ``labels`` (destination names),
    each place also says how many of its people come from each destination.
    Figures are formatted here, so the page and the report print the same.
    """
    rows: list[dict[str, Any]] = []
    first = {"street": 0, "crossing": 0}
    for kind in ("street", "crossing"):
        path = folder / f"exposure_{kind}s.parquet"
        if not found(path):
            continue
        read = gpd.read_parquet(path).sort_values("rank")
        is_first = read.tier.astype(str).eq(text.FIX_FIRST)
        first[kind] = int(is_first.sum())
        shown = read[is_first | read["rank"].le(EXPOSURE_ROWS)]
        for row in shown.to_dict("records"):
            geometry = row["geometry"]
            parts = list(getattr(geometry, "geoms", [geometry]))
            rows.append(
                {
                    "kind": kind,
                    "rank": int(row["rank"]),
                    "tag": text.tag(kind, int(row["rank"])),
                    "label": str(row["label"]),
                    "people": text.people(_finite(row["people"], 12)),
                    "points": text.points(_significant(row["risk_points"])),
                    "length": text.length(float(geometry.length)) if kind == "street" else "",
                    "factors": str(row["risk_factors"]),
                    "tier": str(row["tier"]),
                    "reason": str(row.get("tier_reason") or row["tier"]),
                    "from": from_each(row.get("people_by_destination"), labels or {}),
                    "lines": [_line(p, to_degrees) for p in parts if p.geom_type != "Point"],
                    "spots": [_line(p, to_degrees)[0] for p in parts if p.geom_type == "Point"],
                }
            )
    return {"exposure": rows, "fix_first": first}


def _significant(value: object) -> float | None:
    """Risk points to 12 significant figures, as the report prints them; None when missing."""
    number = float(str(value)) if value is not None else math.nan
    return float(f"{number:.12g}") if math.isfinite(number) else None


def _finite(value: object, digits: int) -> float | None:
    """A number rounded for the page, or None when it is missing or not finite."""
    number = float(str(value)) if value is not None else math.nan
    return round(number, digits) if math.isfinite(number) else None


def _share(helped: object, counted: object) -> float | None:
    """Helped as a percentage of counted, from the unrounded weights; None if undefined."""
    h, c = _finite(helped, 12), _finite(counted, 12)
    if h is None or c is None or c <= 0:
        return None
    pct = 100 * h / c
    return round(pct, 1) if pct < 10 else round(pct)


def _part_note(row: Mapping[Any, Any]) -> str:
    """The best of a whole street's busiest sections, as a note; "" if none."""
    part, saved = row.get("best_part"), row.get("best_part_minutes_saved")
    if not isinstance(part, str) or not part or saved is None or pd.isna(saved):
        return ""
    return (
        f"Best of its {PARTS_PER_STREET} busiest sections: "
        f"{part.split(': ', 1)[-1]}, {float(saved):,.1f} min alone"
    )


def _preset(mode: str, name: str) -> tuple[str, str]:
    try:
        preset = presets.load(mode, name)
    except presets.PresetError:
        return name, ""
    return preset.title, " ".join(preset.source.split())


_title = text.title


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    """A summary row with missing numbers as None, for JSON."""
    clean: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, float):
            clean[str(key)] = round(value, 2) if math.isfinite(value) else None
        else:
            clean[str(key)] = None if pd.isna(value) else value
    return clean
