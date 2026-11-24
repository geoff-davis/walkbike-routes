"""A run's tables and method note, as ``report.md`` and ``report.html``.

Every figure comes from the run's own outputs (``summary.csv``, each run's
``flows.parquet`` and ``screen.parquet``, ``links.parquet`` and
``manifest.json``); the method note comes from each preset's and decay
curve's YAML.  The report is built once as a list of blocks, then written
both ways, so the HTML is escaped in one place and needs no Markdown parser.
"""

from __future__ import annotations

import html
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import quote

import geopandas as gpd
import pandas as pd

from walkbike_routes.analysis.exposure import ALL_DESTINATIONS
from walkbike_routes.analysis.exposure_rules import ExposureRules, points_rows
from walkbike_routes.analysis.screens import (
    PARTS_PER_STREET,
    PRIORITY_ROWS,
    PRIORITY_SHARE,
    priorities,
)
from walkbike_routes.costs import presets
from walkbike_routes.demand import decay
from walkbike_routes.network.sidewalks import match_sidewalks
from walkbike_routes.report import text
from walkbike_routes.report.map import in_order, run_rules
from walkbike_routes.report.text import (
    DRAFT,
    FALLBACK_SOURCES,
    MODE_NAMES,
    NOT_COUNTS,
    SYNTHETIC,
    from_each,
)

TOP_STREETS = 10
ASSUMPTIONS = "docs/assumptions.md"
# Where the tool lives and how its route models work, under the report's title.
ABOUT = (
    ("Made with walkbike-routes (source and docs)", text.REPO),
    ("How the route models work", text.METHODS_DOC),
)
SECTIONS_FILE = "assumption_sections.json"  # shipped in the package: docs/ is not
NO_DECAY = text.NO_DECAY
HOW_STREETS = (
    "A sidewalk counts as the street it runs beside; a crossing is named for the "
    "street it crosses; other unnamed paths are listed by OpenStreetMap way."
)


@dataclass(frozen=True)
class Heading:
    level: int
    text: str


@dataclass(frozen=True)
class Para:
    text: str
    note: bool = False
    warn: bool = False


@dataclass(frozen=True)
class Link:
    """A paragraph that is one link, to a file beside the report."""

    text: str
    href: str


@dataclass(frozen=True)
class Away:
    """A paragraph of text followed by links to pages elsewhere, opened in a new tab."""

    text: str
    links: tuple[tuple[str, str], ...]
    note: bool = False


@dataclass(frozen=True)
class Bullets:
    items: tuple[str, ...]


@dataclass(frozen=True)
class Linked:
    """A cell that is a link to a file beside the report: a place's label, to its map."""

    text: str
    href: str


@dataclass(frozen=True)
class Where:
    """A cell naming a place, with its latitude and longitude for a link to a street map."""

    text: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Lines:
    """A cell of several items, one to a line in the HTML report."""

    items: tuple[str, ...]


@dataclass(frozen=True)
class Noted:
    """A cell with a hover note in the HTML report, such as a route model's name."""

    text: str
    tip: str


Cell = str | Linked | Where | Lines | Noted


@dataclass(frozen=True)
class Table:
    """``tips`` explain the columns; rows after ``more_after`` start hidden in the HTML."""

    header: tuple[str, ...]
    rows: tuple[tuple[Cell, ...], ...]
    numeric: tuple[bool, ...]
    tips: tuple[str, ...] = ()
    more_after: int | None = None


@dataclass(frozen=True)
class Terms:
    """Terms and what they mean, with an optional "more" link for some."""

    items: tuple[tuple[str, str], ...]
    links: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Scatter:
    """Points on log scales, drawn in the HTML page only; values of 0 sit on the floor."""

    title: str
    x_label: str
    y_label: str
    points: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Fold:
    """A section that starts closed in the HTML report; in Markdown, a plain section."""

    level: int
    title: str
    blocks: tuple[Block, ...]


Block = Heading | Para | Link | Away | Bullets | Table | Scatter | Terms | Fold


def build_report(out: Path) -> list[Path]:
    """Write ``report.md`` and ``report.html`` for a run folder; return their paths."""
    manifest = json.loads((out / "manifest.json").read_text())
    blocks = report_blocks(out, manifest)
    title = _report_title(manifest)
    paths = [out / "report.md", out / "report.html"]
    paths[0].write_text(markdown(blocks))
    paths[1].write_text(page(blocks, title))
    return paths


def read_summary(out: Path) -> pd.DataFrame:
    """``summary.csv`` with its identifiers kept as text ("001", "NA")."""
    ids = ["destination_id", "mode", "preset"]
    summary = pd.read_csv(out / "summary.csv")
    summary[ids] = pd.read_csv(out / "summary.csv", usecols=ids, dtype=str, keep_default_na=False)[
        ids
    ]
    return summary


def main_rows(rows: pd.DataFrame) -> pd.DataFrame:
    """Each mode's main run: the first preset listed for it."""
    return rows[~rows.duplicated("mode")] if "mode" in rows else rows


@dataclass
class Sections:
    """One destination's parts of the report: what comes first, and what is reference."""

    main: list[Block] = field(default_factory=list)
    watch: list[Block] = field(default_factory=list)
    times: list[Block] = field(default_factory=list)
    busiest: list[Block] = field(default_factory=list)


def report_blocks(out: Path, manifest: dict[str, Any]) -> list[Block]:
    """The whole report: what to look at first, then the reference tables."""
    summary = in_order(read_summary(out))
    listed = {out / name for name in manifest["files"]}
    links = gpd.read_parquet(out / "links.parquet")
    names = street_names(links)
    destinations = gpd.read_parquet(out / "destinations.parquet")
    labels = dict(
        zip(
            destinations.destination_id.astype(str),
            destinations.get("name", destinations.destination_id).astype(str),
            strict=True,
        )
    )
    caveat = [SYNTHETIC] if manifest.get("synthetic") else []
    blocks: list[Block] = [
        Heading(1, _report_title(manifest)),
        Para(" ".join([*caveat, DRAFT, NOT_COUNTS]), warn=True),
        Away("", ABOUT, note=True),
    ]
    rules = run_rules(manifest)
    has_lists = any(path.name == "exposure_streets.parquet" for path in listed)
    if has_lists:
        any_decayed = bool(summary.get("decay_curve", pd.Series(dtype=str)).map(text.decayed).any())
        route_cost = any(path.name == "screen.parquet" for path in listed)
        terms = tuple(text.key_terms(rules, any_decayed, route_cost))
        more = (("Route-cost minutes", text.ROUTE_COST_DOC),) if route_cost else ()
        blocks += [Heading(2, "Key terms"), Terms(terms, more)]
    together = combined_sections(out, summary, labels, listed, rules)
    parts = [together]
    for destination_id in dict.fromkeys(summary.destination_id):
        rows = summary[summary.destination_id.eq(destination_id)]
        parts.append(
            destination_sections(
                out / destination_id, labels.get(destination_id, destination_id), rows, names,
                listed, rules, brief=bool(together.main), links=links,
            )
        )  # fmt: skip
    for part in parts:
        blocks += part.main
    reference = (
        ("Reference: watch lists", [b for p in parts for b in p.watch], WATCH_NOTE),
        ("Reference: travel times under each route model", [b for p in parts for b in p.times], ""),
        ("Reference: busiest streets", [b for p in parts for b in p.busiest],
         f"{PEAK_FLOW} {HOW_STREETS}"),
    )  # fmt: skip
    for heading, found, note in reference:
        if found:
            blocks.append(Fold(2, heading, (*([Para(note, note=True)] if note else []), *found)))
    if has_lists:
        blocks.append(_fold(rules_blocks(rules)))
    has_screen = any(path.name == "screen.parquet" for path in listed)
    blocks.append(_fold(method_blocks(summary, screen=has_screen)))
    sources = [str(s) for s in manifest.get("sources", FALLBACK_SOURCES)]
    layers_line = local_layers_line(manifest)
    credits: list[Block] = [Bullets(tuple(sources))]
    if layers_line:
        credits.append(Para(layers_line))
    blocks.append(Fold(2, "Data and credits", tuple(credits)))
    return blocks


def _fold(blocks: Sequence[Block]) -> Fold:
    """A section whose first block is its heading, as one that starts closed."""
    first = blocks[0]
    assert isinstance(first, Heading)
    return Fold(first.level, first.text, tuple(blocks[1:]))


def local_layers_line(manifest: dict[str, Any]) -> str:
    """Which city or county layers the run used; empty for a made-up town or an old run."""
    if manifest.get("synthetic") or "local_layers" not in manifest:
        return ""
    layers = manifest["local_layers"]
    if not layers:
        return (
            "No city or county layers were used: apart from any hand corrections, "
            "sidewalks, signals, posted speeds and bike facilities are from "
            "OpenStreetMap only."
        )
    named = "; ".join(f"{layer['name']} ({layer['kind']})" for layer in layers)
    return f"City or county layers used: {named}."


def _map_link(folder: Path, listed: set[Path]) -> list[Block]:
    """A link to a destination's map, when the run wrote one."""
    if not (folder / "map.html").is_file():
        return []
    return [Link("Open the map", f"{quote(folder.name)}/map.html")]


def destination_sections(
    folder: Path,
    name: str,
    rows: pd.DataFrame,
    names: pd.Series,
    listed: set[Path],
    rules: ExposureRules | None = None,
    *,
    brief: bool = False,
    links: gpd.GeoDataFrame | None = None,
) -> Sections:
    """One destination: who can get there, what to fix first, and the reference tables.

    ``brief`` is for a report that also lists every destination's places
    together: each destination then shows its highest-ranked fix-first places
    and leaves its watch list to its map.
    """
    rules = rules or ExposureRules()
    main = main_rows(rows)
    lists = []
    for r in rows.to_dict("records"):
        run = folder / f"{r['mode']}-{r['preset']}"
        along, across = run / "exposure_streets.parquet", run / "exposure_crossings.parquet"
        if along in listed and across in listed:
            lists.append((r, gpd.read_parquet(along), gpd.read_parquet(across)))
    found = Sections()
    found.main = [Heading(2, name), *_map_link(folder, listed)]
    has_map = (folder / "map.html").is_file()
    for sentence in (
        glance(main, [(str(r["mode"]), s, c) for r, s, c in lists]), who_line(main)
    ):  # fmt: skip
        found.main += [Para(sentence)] if sentence else []
    found.main.append(who_table(main))
    found.main += [Para(note, note=True) for note in [unconnected_note(main)] if note]
    for r, streets, crossings in lists:
        mode = _mode_words(r, main)
        unit, is_decayed = text.unit_words(r.get("weight_unit")), text.decayed(r.get("decay_curve"))
        where = (
            (f"{quote(folder.name)}/map.html", f"{r['mode']}-{r['preset']}") if has_map else None
        )
        found.main += fix_first_blocks(
            mode, streets, crossings, unit, is_decayed, rules=rules,
            limit=FIRST_ROWS if brief else None, where=where,
        )  # fmt: skip
        if not brief:
            found.watch += watch_blocks(
                f"{name}: {mode.lower()}", streets, crossings, unit, is_decayed, where=where
            )
    for r in rows.to_dict("records"):
        path = folder / f"{r['mode']}-{r['preset']}" / "screen.parquet"
        if path not in listed:
            continue
        screen = _read_places(path)
        if screen.empty:
            continue
        table = screen_table(
            screen,
            unit=text.unit_words(r.get("weight_unit")),
            is_decayed=text.decayed(r.get("decay_curve")),
        )
        found.main.append(
            Fold(
                3,
                f"{_mode_words(r, main)}: where a change would open new routes",
                (Para(SCREEN_TERMS, note=True), table),
            )
        )
    if len(rows) > len(main) or _has_bands(rows):
        found.times = [Heading(3, name), summary_table(rows)]
    flows = {
        (str(r["mode"]), str(r["preset"])): folder / f"{r['mode']}-{r['preset']}" / "flows.parquet"
        for r in rows.to_dict("records")
    }
    for mode in dict.fromkeys(m for m, _ in flows):
        runs = {p: path for (m, p), path in flows.items() if m == mode and path in listed}
        if not runs:
            continue
        read = {p: gpd.read_parquet(path) for p, path in runs.items()}
        tops = {p: top_streets(flows_, names) for p, flows_ in read.items()}
        spots = (
            {p: top_spots(read[p], names, links, tops[p]) for p in read}
            if links is not None
            else None
        )
        found.busiest.append(Heading(3, f"{name}: {MODE_NAMES.get(mode, mode).lower()}"))
        found.busiest += busiest_blocks(tops, spots, mode)
    return found


def _read_places(path: Path) -> pd.DataFrame:
    """A table of places with its shapes, or without them if it was written without."""
    try:
        return gpd.read_parquet(path)
    except ValueError:  # no geometry metadata: an older run, or a hand-made table
        return pd.read_parquet(path)


Spot = tuple[float, float]  # latitude, longitude


def _spots(table: pd.DataFrame) -> list[Spot | None]:
    """A point on each row's shape, in degrees; None where there is no shape to place."""
    crs = getattr(table, "crs", None) if isinstance(table, gpd.GeoDataFrame) else None
    if crs is None:
        return [None] * len(table)
    middles = gpd.GeoSeries([_middle(shape) for shape in table.geometry], crs=crs).to_crs(4326)
    return [
        None if p is None or p.is_empty else (float(p.coords[0][1]), float(p.coords[0][0]))
        for p in middles
    ]


def _middle(shape: Any) -> Any:
    """Halfway along a line (its longest part), or a point inside any other shape."""
    if shape is None or shape.is_empty:
        return None
    longest = max(getattr(shape, "geoms", [shape]), key=lambda part: part.length)
    if longest.geom_type == "LineString" and longest.length:
        return longest.interpolate(0.5, normalized=True)
    return longest.representative_point()


def _place(name: str, spot: Spot | None) -> Cell:
    return name if spot is None else Where(name, *spot)


def _lines(items: str) -> Cell:
    """Items joined by "; ", as a cell with one to a line."""
    parts = tuple(items.split("; "))
    return Lines(parts) if len(parts) > 1 else items


def who_line(main: pd.DataFrame) -> str:
    """ "Of the 5,440 residents aged 12-13 in the study area:", above the table of modes."""
    records = main.to_dict("records")
    totals = {_float(r.get("total_weight")) for r in records}
    if len(totals) != 1 or not (total := totals.pop()):
        return ""
    return (
        f"Of the {_num(total)} {text.unit_words(records[0].get('weight_unit'))} in the study area:"
    )


def unconnected_note(main: pd.DataFrame) -> str:
    """Why a mode with no time limit reaches fewer than everyone; "" when it reaches all."""
    for r in main.to_dict("records"):
        total, within = _float(r.get("total_weight")), _float(r.get("within_cap_weight"))
        if _float(r.get("time_cap_min")) is not None or not total or within is None:
            continue
        missing = total - within
        if text.percent(within / total) == "100%" or missing <= 0:
            continue
        mode = MODE_NAMES.get(str(r["mode"]), str(r["mode"]))
        return (
            f"{mode} is not 100%: the other {text.people(missing)} "
            f"({text.percent(missing / total)}) are at {text.UNCONNECTED}."
        )
    return ""


def _mode_words(row: Mapping[Any, Any], main: pd.DataFrame) -> str:
    """A run's mode as a heading; a run that is not its mode's main one says which it is."""
    mode, preset = str(row["mode"]), str(row["preset"])
    name = MODE_NAMES.get(mode, mode)
    is_main = bool((main["mode"].astype(str).eq(mode) & main.preset.astype(str).eq(preset)).any())
    return name if is_main else f"{name} ({preset})"


def _has_bands(rows: pd.DataFrame) -> bool:
    return any(re.fullmatch(r"within_(.+)_min_weight", str(c)) for c in rows.columns)


def _limit(row: Mapping[Any, Any]) -> str:
    """ " within 20 minutes", or "" for a run with no time limit."""
    cap = _float(row.get("time_cap_min"))
    return "" if cap is None else f" within {_num(cap, 1)} minutes"


def glance(main: pd.DataFrame, lists: Sequence[tuple[str, pd.DataFrame, pd.DataFrame]]) -> str:
    """How much there is to fix first on a destination's routes, in a sentence."""
    sentences = []
    records = main.to_dict("records")
    counts = []
    for mode, streets, crossings in lists:
        if not any(str(r["mode"]) == mode for r in records):
            continue
        s, c = len(_first(streets)), len(_first(crossings))
        found = f"{text.count(s, 'street')} and {text.count(c, 'crossing')}" if s or c else "none"
        counts.append(f"{found} on the {MODE_NAMES.get(mode, mode).lower()} routes")
    if counts:
        sentences.append(f"To fix first: {'; '.join(counts)}.")
    return " ".join(sentences)


def who_table(main: pd.DataFrame) -> Table:
    """Each mode's main run: how many can get there, and how long it takes."""
    any_decayed = any(text.decayed(r.get("decay_curve")) for r in main.to_dict("records"))
    header = ["Mode", "Time limit", "Can get there", "Share", "Median minutes"]
    header += ["Expected trips"] if any_decayed else []
    body = []
    for r in main.to_dict("records"):
        total, within = _float(r.get("total_weight")), _float(r.get("within_cap_weight"))
        cap = _float(r.get("time_cap_min"))
        cells = [
            MODE_NAMES.get(str(r["mode"]), str(r["mode"])),
            "none" if cap is None else f"{_num(cap, 1)} min",
            _num(within),
            text.percent(within / total) if total and within is not None else "–",
            text.minutes(_float(r.get("median_minutes_within_cap"))),
        ]
        if any_decayed:
            decayed = text.decayed(r.get("decay_curve"))
            cells.append(_num(_float(r.get("expected_weight"))) if decayed else "–")
        body.append(tuple(cells))
    return Table(
        tuple(header), tuple(body), (False, False) + (True,) * (len(header) - 2),
        text.tips(tuple(header)),
    )  # fmt: skip


WATCH_ROWS = 3
FIRST_ROWS = 5  # of each destination's own fix-first places, beside the lists for all together
MORE_SLACK = 2  # a list this much longer than FIRST_ROWS is shown whole
WATCH_NOTE = (
    f"The {WATCH_ROWS} highest-ranked places that are not fix first, and what each is short "
    "of. The maps show more, with the factors behind their points; the full lists are in "
    "each run's exposure_streets.parquet and exposure_crossings.parquet."
)
PEAK_FLOW = "Peak flow is the street's busiest link."


def _first(table: pd.DataFrame) -> pd.DataFrame:
    return table[table.tier.astype(str).eq(text.FIX_FIRST)] if "tier" in table else table.iloc[:0]


def exposure_table(
    table: pd.DataFrame,
    flow: str,
    *,
    streets: bool,
    labels: Mapping[str, str] | None = None,
    reasons: bool = True,
    factors: bool = True,
    where: tuple[str, str] | None = None,
) -> Table:
    """Places by rank, with the factors behind their points.

    ``flow`` heads the column of people.  With ``labels`` (destination names),
    the lists add up several destinations, and a last column says how many
    come from each.  ``reasons`` adds each place's tier and why.  ``where`` is
    the map beside the report and the run's id on it: each label then opens the
    map at its place.
    """
    top = table.sort_values("rank")
    header = ["On the map", "Street" if streets else "Crossing", flow]
    header += ["Length"] if streets else []
    header += ["Risk points"] + (["Why it has these points"] if factors else [])
    header += ["Priority"] if reasons else []
    header += ["From each destination"] if labels is not None else []
    rows = []
    for r, shape, spot in zip(top.to_dict("records"), _shapes(top), _spots(top), strict=True):
        tag = text.tag("street" if streets else "crossing", int(r["rank"]))
        # The map draws every fix-first place and the highest-ranked of the rest.
        drawn = text.on_map(r)
        row: list[Cell] = [
            _on_map(tag, where if drawn else None, spot),
            _place(str(r["label"]), spot),
            text.people(float(r["people"])),
        ]
        if streets:
            row.append(text.length(float(shape.length)) if shape is not None else "–")
        row.append(text.points(float(r["risk_points"])))
        if factors:
            row.append(_lines(str(r["risk_factors"])))
        if reasons:
            row.append(_reason(r))
        if labels is not None:
            row.append(_lines(from_each(r.get("people_by_destination"), labels)))
        rows.append(tuple(row))
    right = tuple(h in (flow, "Length", "Risk points") for h in header)
    return Table(tuple(header), tuple(rows), right, text.tips(tuple(header), flow))


MAP_ZOOM = 17


def _on_map(tag: str, where: tuple[str, str] | None, spot: Spot | None) -> Cell:
    """A place's label, as a link that opens the map at the place under the same route model."""
    if where is None or spot is None:
        return tag
    page_, run = where
    return Linked(tag, f"{page_}#{spot[0]:.5f},{spot[1]:.5f},{MAP_ZOOM},{quote(run)},{tag}")


def _shapes(table: pd.DataFrame) -> list[Any]:
    return list(table.geometry) if "geometry" in table else [None] * len(table)


def _reason(row: Mapping[Any, Any]) -> str:
    """The tier and why, in a sentence; older outputs have only the tier."""
    reason = row.get("tier_reason")
    return str(reason) if isinstance(reason, str) and reason else str(row.get("tier", ""))


def fix_first_blocks(
    mode: str,
    streets: pd.DataFrame,
    crossings: pd.DataFrame,
    unit: str,
    is_decayed: bool,
    labels: Mapping[str, str] | None = None,
    rules: ExposureRules | None = None,
    limit: int | None = None,
    where: tuple[str, str] | None = None,
) -> list[Block]:
    """The fix-first streets and crossings of one run, by rank: all, or the first ``limit``."""
    rules = rules or ExposureRules()
    first_streets, first_crossings = _first(streets), _first(crossings)
    blocks: list[Block] = [Heading(3, f"{mode}: places to fix first")]
    if first_streets.empty and first_crossings.empty:
        if streets.empty and crossings.empty:
            blocks.append(Para("No street or crossing on these routes has risk points."))
        else:
            # Beside the lists for all together, a destination's own watch list is on its map.
            rest = (
                "The rest are on its map."
                if limit
                else "The highest-ranked of the rest are under “Reference: watch lists”."
            )
            blocks.append(
                Para(f"No street or crossing on these routes meets the fix-first rule. {rest}")
            )
        return blocks
    flow = text.flow_heading(unit, is_decayed)
    # With the plain rule a place's people and points are the whole reason; say more otherwise.
    reasons = not text.standard_reason(rules.fix_first)
    for first, is_street in ((first_streets, True), (first_crossings, False)):
        if first.empty:
            continue
        shown = first.sort_values("rank").head(limit) if limit else first
        table = exposure_table(
            shown, flow, streets=is_street, labels=labels, reasons=reasons, where=where
        )
        if len(shown) > FIRST_ROWS + MORE_SLACK:  # a long list opens at its first rows
            table = replace(table, more_after=FIRST_ROWS)
        blocks.append(table)
        if len(shown) < len(first):
            what = "streets" if is_street else "crossings"
            blocks.append(
                Para(
                    f"The {len(shown)} highest-ranked of {len(first):,} {what} to fix first; "
                    "the map shows them all.",
                    note=True,
                )
            )
    return blocks


def watch_blocks(
    title: str,
    streets: pd.DataFrame,
    crossings: pd.DataFrame,
    unit: str,
    is_decayed: bool,
    labels: Mapping[str, str] | None = None,
    where: tuple[str, str] | None = None,
) -> list[Block]:
    """The highest-ranked watch places of one run, for the reference section."""
    blocks: list[Block] = []
    flow = text.flow_heading(unit, is_decayed)
    for table, is_street in ((streets, True), (crossings, False)):
        if "tier" not in table:
            continue
        rest = table[~table.tier.astype(str).eq(text.FIX_FIRST)].sort_values("rank")
        if rest.empty:
            continue
        # The factors and who comes from where are on the map; here, what each place is short of.
        blocks.append(
            exposure_table(
                rest.head(WATCH_ROWS), flow, streets=is_street, factors=False, where=where
            )
        )
        if len(rest) > WATCH_ROWS:
            what = "streets" if is_street else "crossings"
            blocks.append(Para(f"{WATCH_ROWS} of {len(rest):,} watch {what}.", note=True))
    return [Heading(3, title), *blocks] if blocks else []


def combined_blocks(
    out: Path,
    summary: pd.DataFrame,
    labels: Mapping[str, str],
    listed: set[Path],
    rules: ExposureRules | None = None,
) -> list[Block]:
    """The lists with every destination's routes added up, when the run wrote them."""
    return combined_sections(out, summary, labels, listed, rules).main


def combined_sections(
    out: Path,
    summary: pd.DataFrame,
    labels: Mapping[str, str],
    listed: set[Path],
    rules: ExposureRules | None = None,
) -> Sections:
    """The fix-first and watch lists with every destination's routes added up."""
    folder = out / ALL_DESTINATIONS
    runs = sorted(
        {p.parent for p in listed if p.parent.parent == folder},
        key=lambda run: (text.mode_rank(run.name.partition("-")[0]), run.name),
    )
    if summary.destination_id.astype(str).eq(ALL_DESTINATIONS).any():
        runs = []  # a destination of that name: its own runs, and no lists together
    found = Sections()
    where: tuple[str, str] | None = None
    for run in runs:
        streets, crossings = run / "exposure_streets.parquet", run / "exposure_crossings.parquet"
        if streets not in listed or crossings not in listed:
            continue
        mode, _, preset = run.name.partition("-")
        rows = summary[summary["mode"].eq(mode) & summary.preset.eq(preset)]
        units = set(rows.weight_unit.astype(str)) if "weight_unit" in rows else set()
        unit = text.unit_words(units.pop() if len(units) == 1 else "")
        curves = rows.get("decay_curve", pd.Series(dtype=str))
        is_decayed = bool(len(curves)) and bool(curves.map(text.decayed).all())
        if not found.main:
            names = [labels.get(d, d) for d in dict.fromkeys(summary.destination_id)]
            found.main += [
                Heading(2, "All destinations together"),
                *_map_link(folder, listed),
                Para(
                    f"Every destination's routes added up ({', '.join(names)}). A place is "
                    "judged on the total; the last column says how many come from each."
                ),
            ]
            where = (
                (f"{quote(folder.name)}/map.html", "") if (folder / "map.html").is_file() else None
            )
        name = MODE_NAMES.get(mode, mode)
        found.main += fix_first_blocks(
            name, gpd.read_parquet(streets), gpd.read_parquet(crossings), unit, is_decayed,
            labels, rules, where=(where[0], run.name) if where else None,
        )  # fmt: skip
        found.watch += watch_blocks(
            f"All destinations together: {name.lower()}", gpd.read_parquet(streets),
            gpd.read_parquet(crossings), unit, is_decayed, labels,
            where=(where[0], run.name) if where else None,
        )  # fmt: skip
    return found


def rules_blocks(rules: ExposureRules) -> list[Block]:
    """The risk points this run used, so its lists can be checked."""
    rows = tuple((factor, points) for factor, points in points_rows(rules.points))
    return [
        Heading(2, "Risk points used"),
        Para(
            "Each place's risk points are the sum of these factors. The values are "
            "hand-set from the systemic safety guides (FHWA-SA-13-019; NCHRP Report 893) "
            "and the level of traffic stress (Mekuria, Furth & Nixon 2012), and can be "
            f"changed under exposure in the study config. See “Where routes meet traffic "
            f"today” in {ASSUMPTIONS}."
        ),
        Table(("Factor", "Points"), rows, (False, False), text.tips(("Factor", "Points"))),
    ]


def summary_table(rows: pd.DataFrame) -> Table:
    """Every mode and preset's catchment figures, with the travel-time bands."""
    bands = sorted(
        {
            float(m.group(1))
            for c in rows.columns
            if (m := re.fullmatch(r"within_(.+)_min_weight", c))
        }
    )
    header = ["Mode", "Route model", "In study area", "Can get there", "Share", "Median minutes"]
    header += [f"≤ {_num(b, 1)} min" for b in bands]
    header += ["Expected trips"]
    body = []
    for r in rows.to_dict("records"):
        total, within = _float(r.get("total_weight")), _float(r.get("within_cap_weight"))
        cap = _float(r.get("time_cap_min"))
        limit = f"{_num(within)} (≤ {_num(cap, 1)} min)" if cap is not None else _num(within)
        share = text.percent(within / total) if total and within is not None else "–"
        mode, preset = str(r["mode"]), str(r["preset"])
        model: Cell = Noted(preset, tip) if (tip := text.model_tip(mode, preset)) else preset
        cells: list[Cell] = [MODE_NAMES.get(mode, mode), model, _num(total), limit, share]
        # Runs from before the column show "–", not the all-homes median under this label.
        cells.append(text.minutes(_float(r.get("median_minutes_within_cap"))))
        cells += [_num(_float(r.get(_band_column(rows, b)))) for b in bands]
        expected = _float(r.get("expected_weight"))
        cells.append(_num(expected) if text.decayed(r.get("decay_curve")) else "–")
        body.append(tuple(cells))
    numeric = (False, False) + (True,) * (len(header) - 2)
    return Table(tuple(header), tuple(body), numeric, text.tips(tuple(header)))


UNNAMED = "\x00"  # an unnamed link's key: its label + UNNAMED + its OSM way (or link id)


def street_names(links: gpd.GeoDataFrame) -> pd.Series:
    """Each link's street, by link id, for the busiest-streets tables.

    - A named link is its street.
    - An unnamed sidewalk is the street it runs beside
      (:func:`walkbike_routes.network.sidewalks.match_sidewalks`), as Broach's
      walking model treats it.
    - An unnamed crossing is "crossing of <street>", named from a street of
      the crossed class that meets it at a node.
    - Any other unnamed link is grouped by OpenStreetMap way, not by class:
      two unnamed footways across town are not one street, while one path
      split into many links is.

    :func:`label` turns a key back into text.
    """
    ids = links.link_id.astype(str)
    name = links["name"] if "name" in links else pd.Series(None, index=links.index)
    has_name = name.notna() & name.astype(str).str.strip().ne("")
    named = name.astype(str).where(has_name, "")
    highway = (
        links["highway"].astype(str)
        if "highway" in links
        else pd.Series("street", index=links.index)
    )
    footway = (
        links.footway.fillna("").astype(str)
        if "footway" in links
        else pd.Series("", index=links.index)
    )
    is_crossing = is_crossing_link(links)
    kind = ("unnamed " + highway.replace("service", "service road")).mask(
        footway.eq("sidewalk"), "unnamed sidewalk"
    )
    kind = kind.mask(is_crossing, "unnamed crossing")
    way = "link " + ids
    if "osm_way_id" in links:
        way = ("OSM way " + links.osm_way_id.astype("string")).fillna(way)
    key = named.where(has_name, kind + UNNAMED + way)
    sidewalk = ~has_name & footway.eq("sidewalk")
    if sidewalk.any() and links.crs is not None and links.crs.is_projected:
        beside = match_sidewalks(links).beside_name
        key = key.mask(sidewalk & beside.ne(""), beside)
    crossing = ~has_name & is_crossing
    if crossing.any() and {"u", "v"} <= set(links.columns):
        crossed = _crossed_names(links, has_name)
        found = crossed.reindex(links.index).fillna("")
        key = key.mask(crossing & found.ne(""), "crossing of " + found)
    return pd.Series(key.astype(str).to_numpy(), index=ids.to_numpy())


def is_crossing_link(links: gpd.GeoDataFrame) -> pd.Series:
    """Links of a crossing way: the road crossed is known, or OSM tags it a crossing."""
    found = pd.Series(False, index=links.index)
    if "crossed_road" in links:
        found |= links.crossed_road.fillna("").astype(str).ne("")
    if "footway" in links:
        found |= links.footway.fillna("").astype(str).eq("crossing")
    return found


def _crossed_names(links: gpd.GeoDataFrame, has_name: pd.Series) -> pd.Series:
    """For each unnamed crossing link, the name of a crossed-class street at its ends."""
    is_crossing = is_crossing_link(links)
    streets = links[has_name & ~is_crossing]
    at_node = pd.concat(
        [
            pd.DataFrame(
                {
                    "node": streets[end].astype(str),
                    "highway": streets.highway.astype(str),
                    "name": streets["name"].astype(str),
                }
            )  # fmt: skip
            for end in ("u", "v")
        ]
    ).drop_duplicates(["node", "highway"])
    lookup = {(n, h): nm for n, h, nm in at_node.itertuples(index=False)}
    crossings = links[is_crossing]
    found = {}
    for index, u, v, road in zip(
        crossings.index, crossings.u.astype(str), crossings.v.astype(str),
        crossings.crossed_road.fillna("").astype(str), strict=True,
    ):  # fmt: skip
        hit = lookup.get((u, road)) or lookup.get((v, road))
        if hit:
            found[index] = hit
    return pd.Series(found, dtype=object)


def label(key: str) -> str:
    """A street key as text: an unnamed one says which way or link it is."""
    kind, _, where = key.partition(UNNAMED)
    return f"{kind} ({where})" if where else kind


def top_streets(flows: pd.DataFrame, names: pd.Series, n: int = TOP_STREETS) -> pd.Series:
    """The ``n`` streets with the highest flow on any of their links, highest first.

    A street is all links of one name; its flow is its busiest link's, so a long
    street does not outrank a short busy one by length alone.
    """
    flow, named = _street_flows(flows, names)
    by_street = flow.groupby(named.to_numpy()).max()
    return by_street.sort_values(ascending=False, kind="stable").head(n)


def _street_flows(flows: pd.DataFrame, names: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Each used link's flow and its street, by link id."""
    flow = flows.set_index(flows.physical_edge_id.astype(str)).potential_flow.astype(float)
    flow = flow[flow > 0]
    named = names.reindex(flow.index)
    named = named.fillna(pd.Series("unnamed street" + UNNAMED, index=flow.index) + flow.index)
    return flow, named


def top_spots(
    flows: pd.DataFrame, names: pd.Series, links: gpd.GeoDataFrame, top: pd.Series
) -> dict[str, Spot]:
    """Where each of the top streets is busiest: the middle of its busiest link."""
    flow, named = _street_flows(flows, names)
    if flow.empty or top.empty:
        return {}
    busiest = flow.groupby(named.to_numpy()).idxmax().reindex(top.index).dropna()
    shapes = links.set_index(links.link_id.astype(str)).geometry.reindex(busiest.to_numpy())
    spots = _spots(gpd.GeoDataFrame(geometry=list(shapes), crs=links.crs))
    return {str(s): spot for s, spot in zip(busiest.index, spots, strict=True) if spot}


def busiest_blocks(
    tops: dict[str, pd.Series],
    spots: Mapping[str, Mapping[str, Spot]] | None = None,
    mode: str = "",
) -> list[Block]:
    """The top streets under each preset of one mode, and which agree."""
    presets_ = list(tops)
    spots = spots or {}
    if len(presets_) == 1:
        only = tops[presets_[0]]
        at = spots.get(presets_[0], {})
        single = tuple(
            (str(i + 1), _place(label(str(s)), at.get(str(s))), _num(f, 1))
            for i, (s, f) in enumerate(only.items())
        )
        header = ("#", "Street", "Peak flow")
        return [Table(header, single, (True, False, True), text.tips(header))]
    every = set.intersection(*(set(t.index) for t in tops.values()))
    depth = max(len(t) for t in tops.values())
    rows: list[tuple[Cell, ...]] = []
    for i in range(depth):
        cells: list[Cell] = [str(i + 1)]
        for p in presets_:
            top = tops[p]
            if i < len(top):
                street = str(top.index[i])
                mark = "" if street in every else " †"
                cells.append(
                    _place(
                        f"{label(street)} ({_num(float(top.iloc[i]), 1)}){mark}",
                        spots.get(p, {}).get(street),
                    )
                )
            else:
                cells.append("")
        rows.append(tuple(cells))
    note = (
        f"{len(every)} of these streets are in the top {TOP_STREETS} under every preset. "
        "† marks a street in the top under only some presets: where it ranks depends "
        "on the route-choice model."
    )
    return [
        Table(
            ("#", *presets_),
            tuple(rows),
            (True,) + (False,) * len(presets_),
            text.tips(("#", *presets_), models={p: text.model_tip(mode, p) for p in presets_}),
        ),
        Para(note, note=True),
    ]


def screen_table(
    screen: pd.DataFrame, n: int = PRIORITY_ROWS, *, unit: str = "people", is_decayed: bool = False
) -> Table:
    """The screen's best places for each kind of change, and the share of people each helps."""
    rows = []
    for _, best in priorities(screen, n).groupby("treatment", sort=False):
        for r, spot in zip(best.to_dict("records"), _spots(best), strict=True):
            helped, counted = float(r["benefiting_weight"]), _float(r.get("analyzed_weight"))
            share = text.percent(helped / counted) if counted else "–"
            rows.append(
                (
                    str(r["treatment"]),
                    str(int(r["rank"])),
                    _place(str(r["label"]) + _best_part(r), spot),
                    text.people(helped),
                    share,
                    text.minutes(float(r["weighted_generalized_minutes_saved"])),
                )
            )
    helped_heading = f"{text.flow_heading(unit, is_decayed)} helped"
    header = (
        "Change", "#", "Place", helped_heading, "Share of those counted", text.ROUTE_COST_SAVED
    )  # fmt: skip
    return Table(
        header,
        tuple(rows),
        (False, True, False, True, True, True),
        text.tips(header, text.flow_heading(unit, is_decayed)),
    )


SCREEN_TERMS = (
    "Each change is tried on its own. Helped: those whose route gets cheaper. "
    f"{text.ROUTE_COST_SAVED}: the drop in route cost over everyone helped. {text.ROUTE_COST}."
)


SCREEN_NOTE = (
    "Barriers (where a change would open new routes): each change is tried on its own, and "
    "ranked within its kind by the route-cost minutes it saves, added up over everyone "
    "helped: the route model's cost (often called generalized cost), in minutes, which "
    "counts stress and crossings as well as clock time. Helped: those "
    "whose route gets cheaper, many of whom would switch to the place only once it is treated. "
    f"For each kind of change, the report lists up to {PRIORITY_ROWS} places, leaving out any "
    f"that save less than {PRIORITY_SHARE:.0%} as much as the best of that kind. Kinds are not "
    "ranked against each other: the screen knows what a change saves, not what it costs. "
    "A screen of where to look, not a design."
)


def _best_part(row: Mapping[Any, Any]) -> str:
    """The best of a whole street's busiest sections, for its row; "" if none."""
    part, saved = row.get("best_part"), row.get("best_part_minutes_saved")
    if not isinstance(part, str) or not part or saved is None or pd.isna(saved):
        return ""
    return (
        f"; best of its {PARTS_PER_STREET} busiest sections: "
        f"{part.split(': ', 1)[-1]}, {_num(float(saved), 1)} min alone"
    )


def method_blocks(summary: pd.DataFrame, *, screen: bool = False) -> list[Block]:
    """How the figures were made, from the presets' and curves' own descriptions."""
    active = summary[summary["mode"].isin(["walk", "bike"])]
    used = active.get("decay_curve", pd.Series("", index=active.index)).fillna("").astype(str)
    off = ~used.map(text.decayed)
    runs = active["mode"].astype(str) + "/" + active["preset"].astype(str)
    route = (
        "Walking and cycling: each home's lowest-cost route under the preset; the home "
        "counts if that route fits the mode's time limit."
    )
    if not off.any():
        route += (
            " Its people are weighted by a distance-decay share at the route's length,"
            " then split across near-best routes with a path-size logit."
        )
    elif off.all():
        route += (
            " Distance decay is off: everyone within the limit counts in full, split"
            " across near-best routes with a path-size logit."
        )
    else:
        route += (
            " Its people are weighted by a distance-decay share at the route's length,"
            " except in " + ", ".join(dict.fromkeys(runs[off])) + ", where distance decay"
            " is off and everyone within the limit counts in full. Either way they are"
            " split across near-best routes with a path-size logit."
        )
    route += " Flows add up those weights on each street."
    bullets = [route] if len(active) else []
    if summary["mode"].eq("car").any():
        bullets.append("Driving: the fastest turn-aware route there and back; everyone counts.")
    counts = "The catchment counts (all, within the limit, within each band) are never decayed."
    if (~off).any():
        counts += (
            " Expected trips are the decayed total: what typical national shares would"
            " give, not a forecast."
        )
    bullets += [counts, *([SCREEN_NOTE] if screen else [])]
    bullets.append(f"Every assumption and its source: {ASSUMPTIONS}.")
    blocks: list[Block] = [
        Heading(2, "Method"),
        Bullets(tuple(bullets)),
        Heading(3, "Route-choice presets"),
    ]
    sections = shipped_sections()
    for mode, preset in dict.fromkeys(zip(summary["mode"], summary["preset"], strict=True)):
        key = f"{mode}/{preset}"
        try:
            found = presets.load(str(mode), str(preset))
        except presets.PresetError:
            blocks.append(Para(f"{key}: not a shipped preset; no description."))
            continue
        where = sections.get(key) or sections.get(f"{mode}/*")
        see = f" See {ASSUMPTIONS}, “{where}”." if where else ""
        blocks.append(
            Away(f"{key}: {found.title}. {' '.join(found.source.split())}{see}",
                 (("How it works", text.model_doc(str(mode), str(preset))),))
        )  # fmt: skip
    curves = [
        str(c)
        for c in dict.fromkeys(summary.get("decay_curve", pd.Series(dtype=str)).dropna())
        if str(c) not in ("", NO_DECAY)
    ]
    if curves:
        blocks.append(Heading(3, "Distance-decay curves"))
        for name in curves:
            try:
                curve = decay.load(name)
            except decay.DecayError:
                blocks.append(Para(f"{name}: not a shipped curve; no description."))
                continue
            blocks.append(
                Para(
                    f"{name}: {curve.title}; half of trips are under "
                    f"{curve.median_miles:.2f} miles ({curve.trips:,} trips). "
                    f"{' '.join(curve.source.split())}"
                )
            )
    return blocks


def shipped_sections() -> dict[str, str]:
    """``mode/preset`` → its section of docs/assumptions.md, as shipped in the package.

    An installed package has no docs folder, so the headings are kept beside
    this module; a test checks they match :func:`assumption_sections`.
    """
    shipped = resources.files("walkbike_routes.report").joinpath(SECTIONS_FILE).read_text()
    sections: dict[str, str] = json.loads(shipped)
    return sections


def assumption_sections(path: Path) -> dict[str, str]:
    """``mode/preset`` → the heading of its section in an assumptions file."""
    found: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
            for key in re.findall(r"`([a-z]+/[a-z0-9*-]+)`", heading):
                found.setdefault(key, heading.replace("`", ""))
    return found


def street_map_url(lat: float, lon: float) -> str:
    """A link that opens Google Maps at a place."""
    return f"https://www.google.com/maps/search/?api=1&query={lat:.5f},{lon:.5f}"


def markdown(blocks: Sequence[Block]) -> str:
    """The report as GitHub-flavoured Markdown."""
    return "\n\n".join(_markdown_parts(blocks)) + "\n"


def _markdown_parts(blocks: Sequence[Block]) -> list[str]:
    parts = []
    for block in blocks:
        if isinstance(block, Heading):
            parts.append(f"{'#' * block.level} {_md(block.text)}")
        elif isinstance(block, Fold):
            parts.append(f"{'#' * block.level} {_md(block.title)}")
            parts += _markdown_parts(block.blocks)
        elif isinstance(block, Para):
            parts.append(f"_{_md(block.text)}_" if block.note else _md(block.text))
        elif isinstance(block, Link):
            parts.append(f"[{_md(block.text)}]({quote(block.href, safe='/%')})")
        elif isinstance(block, Away):
            links = " · ".join(f"[{_md(t)}]({href})" for t, href in block.links)
            said = _md(block.text)
            parts.append(
                " ".join(p for p in (f"_{said}_" if block.note and said else said, links) if p)
            )
        elif isinstance(block, Bullets):
            parts.append("\n".join(f"- {_md(item)}" for item in block.items))
        elif isinstance(block, Terms):
            more = dict(block.links)
            parts.append(
                "\n".join(
                    f"- **{_md(t)}:** {_md(d)}" + (f" [More]({more[t]})" if t in more else "")
                    for t, d in block.items
                )
            )
        elif isinstance(block, Scatter):
            count = len(block.points)
            parts.append(f"_{_md(block.title)}: a plot of {count} sites, in the HTML report._")
        else:
            lines = ["| " + " | ".join(_cell(h) for h in block.header) + " |"]
            lines.append("|" + "|".join("--:" if n else "---" for n in block.numeric) + "|")
            lines += ["| " + " | ".join(_cell_md(c) for c in row) + " |" for row in block.rows]
            parts.append("\n".join(lines))
    return parts


def _cell_md(cell: Cell) -> str:
    if isinstance(cell, Linked):
        return f"[{_cell(cell.text)}]({cell.href})"
    if isinstance(cell, Where):
        return f"{_cell(cell.text)} ([Google Maps]({street_map_url(cell.lat, cell.lon)}))"
    if isinstance(cell, Lines):
        return _cell("; ".join(cell.items))
    if isinstance(cell, Noted):
        return _cell(cell.text)
    return _cell(cell)


STYLE = """
:root{--bg:#fff;--fg:#1d1d1f;--muted:#5f6368;--line:#d9d9de;--warn:#fff4d6;--soft:#f5f5f7;--link:#1a56b0}
@media (prefers-color-scheme:dark){:root{--bg:#18181a;--fg:#ececee;--muted:#a3a3a8;--line:#3a3a3f;--warn:#4a3d12;--soft:#232326;--link:#8ab4f8}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:72rem;padding:16px}
a{color:var(--link)}
h1{font-size:1.6rem;margin-bottom:.4rem}h2{font-size:1.3rem;margin-top:2.2rem;border-bottom:1px solid var(--line)}h3{font-size:1.05rem;margin-top:1.4rem}
.note{color:var(--muted);font-size:.9em}.warn{background:var(--warn);padding:.4rem .6rem;font-size:.9em;border-radius:4px}
nav{font-size:.9em;margin:.6rem 0}nav a{margin-right:1rem;white-space:nowrap}
dl.terms{background:var(--soft);border-radius:6px;padding:.5rem .8rem;margin:.5rem 0}
dl.terms div{display:grid;grid-template-columns:8rem 1fr;gap:.6rem;padding:.25rem 0}
dl.terms dt{font-weight:600}dl.terms dd{margin:0}
.wrap{overflow-x:auto}table{border-collapse:collapse;margin:.5rem 0;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line);padding:.3rem .6rem;text-align:left;vertical-align:top}
th{font-size:.85em;color:var(--muted);vertical-align:bottom}
.n{text-align:right;white-space:nowrap}
[data-tip]{border-bottom:1px dotted var(--muted);cursor:help}
#tip{position:fixed;z-index:9;max-width:20rem;background:var(--fg);color:var(--bg);padding:.4rem .6rem;border-radius:4px;font-size:.85rem;line-height:1.35}
a.tag{display:inline-block;min-width:2.1em;text-align:center;padding:0 .4em;border:1px solid var(--link);border-radius:1em;text-decoration:none;font-weight:600;font-size:.85em}
a.ext{font-size:.8em;margin-left:.4em;white-space:nowrap}
ul.f{margin:0;padding:0;list-style:none}ul.f li+li{margin-top:.2rem}
button.more{font:inherit;font-size:.9em;color:var(--link);background:none;border:1px solid var(--line);border-radius:4px;padding:.2rem .6rem;cursor:pointer;margin-bottom:.5rem}
details{margin-top:1rem}summary{cursor:pointer}summary>h2,summary>h3{display:inline;border:0}
details.l2{border-top:1px solid var(--line);padding-top:.5rem}details.l2>summary>h2{font-size:1.1rem}
figure{display:inline-block;margin:.5rem 1rem .5rem 0}.plot{max-width:100%;height:auto;font-size:10px;fill:var(--fg)}
.plot .g{stroke:var(--line)}.plot .p circle{fill:#3b6fb6;fill-opacity:.55}.plot .t{font-weight:600;font-size:11px}
@media (max-width:40rem){dl.terms div{grid-template-columns:1fr;gap:0}}
@media print{body{font-size:11px;max-width:none}.wrap{overflow:visible}h2{break-after:avoid}table{break-inside:auto}tr{break-inside:avoid}
nav,button.more,#tip,a.ext{display:none}tbody.more[hidden]{display:table-row-group}[data-tip]{border:0}}
"""

# Notes on hover or focus, the rest of a long table, and folds opened for printing or a link.
SCRIPT = """
(function(){var t=document.getElementById("tip");
function show(e){var el=e.target.closest&&e.target.closest("[data-tip]");if(!el)return;
t.textContent=el.dataset.tip;t.hidden=false;var r=el.getBoundingClientRect();
t.style.left=Math.max(8,Math.min(r.left,innerWidth-t.offsetWidth-8))+"px";
t.style.top=(r.bottom+6+t.offsetHeight>innerHeight?r.top-6-t.offsetHeight:r.bottom+6)+"px";}
function hide(){t.hidden=true;}
["mouseover","focusin"].forEach(function(n){document.addEventListener(n,show);});
["mouseout","focusout"].forEach(function(n){document.addEventListener(n,hide);});
document.addEventListener("scroll",hide,true);
document.addEventListener("click",function(e){var b=e.target.closest("button.more");if(!b)return;
var m=b.closest(".wrap").querySelector("tbody.more");m.hidden=!m.hidden;
b.textContent=m.hidden?b.dataset.more:b.dataset.less;});
function reveal(){var el=location.hash.length>1&&document.getElementById(decodeURIComponent(location.hash.slice(1)));
if(el&&el.tagName==="DETAILS"){el.open=true;el.scrollIntoView();}}
addEventListener("hashchange",reveal);reveal();
var shut=[];
addEventListener("beforeprint",function(){shut=[].slice.call(document.querySelectorAll("details:not([open])"));
shut.forEach(function(d){d.open=true;});});
addEventListener("afterprint",function(){shut.forEach(function(d){d.open=false;});shut=[];});})();
"""


def page(blocks: Sequence[Block], title: str) -> str:
    """The report as one self-contained HTML page."""
    ids: list[tuple[str, str]] = []
    body = _html_parts(blocks, ids)
    contents = " ".join(f'<a href="#{anchor}">{html.escape(name)}</a>' for name, anchor in ids)
    nav = [f"<nav>{contents}</nav>"] if len(ids) > 1 else []
    # After the title and the caveat: where things are.
    lead = 2 if len(body) > 1 and body[1].startswith('<p class="warn"') else 1
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{STYLE}</style></head><body>\n"
        + "\n".join([*body[:lead], *nav, *body[lead:]])
        + f'\n<div id="tip" role="tooltip" hidden></div><script>{SCRIPT}</script>'
        + "\n</body></html>\n"
    )


def _anchor(name: str, ids: list[tuple[str, str]]) -> str:
    """An id for a section of the page, remembered for the list of contents."""
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "section"
    anchor, n = base, 2
    while any(anchor == used for _, used in ids):
        anchor, n = f"{base}-{n}", n + 1
    ids.append((name, anchor))
    return anchor


def _html_parts(blocks: Sequence[Block], ids: list[tuple[str, str]] | None = None) -> list[str]:
    """Each block as HTML; ``ids`` collects the top sections' anchors, for the contents."""
    parts = []
    for block in blocks:
        if isinstance(block, Heading):
            mark = (
                f' id="{_anchor(block.text, ids)}"' if ids is not None and block.level == 2 else ""
            )
            parts.append(f"<h{block.level}{mark}>{html.escape(block.text)}</h{block.level}>")
        elif isinstance(block, Fold):
            mark = (
                f' id="{_anchor(block.title, ids)}"' if ids is not None and block.level == 2 else ""
            )
            inner = "\n".join(_html_parts(block.blocks))
            parts.append(
                f'<details class="l{block.level}"{mark}><summary><h{block.level}>'
                f"{html.escape(block.title)}</h{block.level}></summary>\n{inner}\n</details>"
            )
        elif isinstance(block, Para):
            cls = ' class="note"' if block.note else ' class="warn"' if block.warn else ""
            parts.append(f"<p{cls}>{_glossed(block.text)}</p>")
        elif isinstance(block, Link):
            href = html.escape(quote(block.href, safe="/%"), quote=True)
            parts.append(f'<p><a href="{href}">{html.escape(block.text)}</a></p>')
        elif isinstance(block, Away):
            links = " · ".join(
                f'<a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener">'
                f"{html.escape(t)}</a>"
                for t, href in block.links
            )
            cls = ' class="note"' if block.note else ""
            said = _glossed(block.text)
            parts.append(f"<p{cls}>{' '.join(p for p in (said, links) if p)}</p>")
        elif isinstance(block, Bullets):
            items = "".join(f"<li>{_glossed(i)}</li>" for i in block.items)
            parts.append(f"<ul>{items}</ul>")
        elif isinstance(block, Terms):
            more = dict(block.links)
            items = "".join(
                f"<div><dt>{html.escape(t)}</dt><dd>{html.escape(d)}"
                + (
                    f' <a href="{html.escape(more[t])}" target="_blank" rel="noopener">More</a>'
                    if t in more
                    else ""
                )
                + "</dd></div>"
                for t, d in block.items
            )
            parts.append(f'<dl class="terms">{items}</dl>')
        elif isinstance(block, Scatter):
            parts.append(scatter_svg(block))
        else:
            parts.append(_table_html(block))
    return parts


def _table_html(table: Table) -> str:
    tips = table.tips or ("",) * len(table.header)
    head = "".join(
        f"<th{' class=n' if n else ''}>{_noted(h, tip)}</th>"
        for h, n, tip in zip(table.header, table.numeric, tips, strict=True)
    )
    rows = [
        "<tr>"
        + "".join(
            f"<td{' class=n' if n else ''}>{_cell_html(c)}</td>"
            for c, n in zip(row, table.numeric, strict=True)
        )
        + "</tr>"
        for row in table.rows
    ]
    cut = table.more_after if table.more_after and table.more_after < len(rows) else len(rows)
    body = f"<tbody>{''.join(rows[:cut])}</tbody>"
    button = ""
    if cut < len(rows):
        body += f'<tbody class="more" hidden>{"".join(rows[cut:])}</tbody>'
        more, fewer = f"Show all {len(rows)}", f"Show the first {cut}"
        button = (
            f'<button type="button" class="more" data-more="{more}" data-less="{fewer}">'
            f"{more}</button>"
        )
    return f'<div class="wrap"><table><thead><tr>{head}</tr></thead>{body}</table>{button}</div>'


def _noted(heading: str, tip: str) -> str:
    """A column heading, with what it means shown on hover or focus."""
    if not tip:
        return html.escape(heading)
    return f'<span tabindex="0" data-tip="{html.escape(tip, quote=True)}">{html.escape(heading)}</span>'


def _cell_html(cell: Cell) -> str:
    if isinstance(cell, Linked):
        return f'<a class="tag" href="{html.escape(cell.href, quote=True)}">{html.escape(cell.text)}</a>'
    if isinstance(cell, Where):
        url = html.escape(street_map_url(cell.lat, cell.lon), quote=True)
        return (
            f'{_glossed(cell.text)} <a class="ext" href="{url}" target="_blank" '
            'rel="noopener noreferrer">Google Maps</a>'
        )
    if isinstance(cell, Lines):
        return '<ul class="f">' + "".join(f"<li>{_glossed(i)}</li>" for i in cell.items) + "</ul>"
    if isinstance(cell, Noted):
        return _noted(cell.text, cell.tip)
    return _glossed(cell)


def _glossed(plain: str) -> str:
    """Escaped text, with each glossary term's first use defined on hover or focus."""
    seen: set[str] = set()

    def note(m: re.Match[str]) -> str:
        term = m.group(1).lower()
        if term in seen:
            return m.group(0)
        seen.add(term)
        tip = html.escape(text.GLOSSARY[term], quote=True)
        return f'<span tabindex="0" data-tip="{tip}">{m.group(0)}</span>'

    # The terms hold nothing html.escape changes, so they match the escaped text.
    return text.GLOSS_PATTERN.sub(note, html.escape(plain))


def scatter_svg(block: Scatter, width: int = 380, height: int = 320) -> str:
    """A log-log scatter as inline SVG, with axes labelled at powers of ten."""
    left, bottom, top, right = 52, 40, 28, 12
    plot_w, plot_h = width - left - right, height - top - bottom
    xs = [x for x, _ in block.points if math.isfinite(x)]
    ys = [y for _, y in block.points if math.isfinite(y)]

    def axis(values: list[float]) -> tuple[float, float]:
        positive = [v for v in values if v > 0]
        if not positive:
            return 0.0, 1.0
        low = math.floor(math.log10(min(positive) / 2))
        high = math.ceil(math.log10(max(positive)))
        return float(low), float(max(high, low + 1))

    (x0, x1), (y0, y1) = axis(xs), axis(ys)

    def place(value: float, low: float, high: float, size: float) -> float:
        level = math.log10(value) if value > 0 else low
        return (level - low) / (high - low) * size

    marks = []
    for x, y in block.points:
        if math.isfinite(x) and math.isfinite(y):
            cx = left + place(x, x0, x1, plot_w)
            cy = top + plot_h - place(y, y0, y1, plot_h)
            marks.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="2.5"/>')
    ticks = []
    for power in range(int(x0), int(x1) + 1):
        tx = left + (power - x0) / (x1 - x0) * plot_w
        ticks.append(
            f'<line x1="{tx:.1f}" y1="{top}" x2="{tx:.1f}" y2="{top + plot_h}" class="g"/>'
            f'<text x="{tx:.1f}" y="{top + plot_h + 14}" text-anchor="middle">{10.0**power:g}</text>'
        )
    for power in range(int(y0), int(y1) + 1):
        ty = top + plot_h - (power - y0) / (y1 - y0) * plot_h
        ticks.append(
            f'<line x1="{left}" y1="{ty:.1f}" x2="{left + plot_w}" y2="{ty:.1f}" class="g"/>'
            f'<text x="{left - 4}" y="{ty + 4:.1f}" text-anchor="end">{10.0**power:g}</text>'
        )
    return (
        f'<figure><svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-label="{html.escape(block.title)}" class="plot">'
        f'<text x="{left}" y="16" class="t">{html.escape(block.title)}</text>'
        + "".join(ticks)
        + f'<g class="p">{"".join(marks)}</g>'
        f'<text x="{left + plot_w / 2:.0f}" y="{height - 6}" text-anchor="middle">'
        f"{html.escape(block.x_label)}</text>"
        f'<text transform="translate(12 {top + plot_h / 2:.0f}) rotate(-90)" '
        f'text-anchor="middle">{html.escape(block.y_label)}</text></svg></figure>'
    )


def _report_title(manifest: dict[str, Any]) -> str:
    study = str(manifest.get("study") or "")
    return text.title("Route access report", study) if study else "Route access report"


def _band_column(rows: pd.DataFrame, band: float) -> str:
    for column in (f"within_{_num(band, 1)}_min_weight", f"within_{band}_min_weight"):
        if column in rows:
            return column
    return f"within_{int(band)}_min_weight"


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


_num = text.number


_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>|])")


def _md(text: str) -> str:
    """Text with Markdown and inline-HTML characters escaped."""
    escaped = _MD_SPECIAL.sub(r"\\\1", text)
    return "\\" + escaped if escaped.startswith(("#", "-", "+", ">")) else escaped


def _cell(text: str) -> str:
    """Table text: no line-start escapes are needed inside a cell."""
    return _MD_SPECIAL.sub(r"\\\1", text).replace("\n", " ")
