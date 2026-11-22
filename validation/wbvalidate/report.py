"""Compare a run's network flows with counts and write the validation report.

``python validation/run.py counts --out <run> --counts <file or dvrpc>`` reads the
run's ``network/<mode>-<preset>/flows.parquet`` (a run with
``network_flows``), matches each count site to its street
(:mod:`wbvalidate.match`), scores each run
(:mod:`wbvalidate.compare`) and writes ``<run>/validation/``:

- ``sites.parquet``: every site with its status, matched links and flow
  under each run.  It holds the count records: keep it private;
- ``report.md`` and ``report.html``: the scores, breakdowns and plots.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry.base import BaseGeometry

from walkbike_routes.exceptions import AccessibilityError
from walkbike_routes.report.tables import (
    Block,
    Bullets,
    Heading,
    Para,
    Scatter,
    Table,
    markdown,
    page,
)
from walkbike_routes.report.text import SYNTHETIC
from wbvalidate import compare
from wbvalidate.match import MATCHED, match

FOLDER = "validation"
MODE_NAMES = {"walk": "Walking", "bike": "Cycling"}
YEAR_BANDS = ((0, 2014, "2014 and before"), (2015, 2019, "2015 to 2019"), (2020, 9999, "2020 on"))


def network_flows(out: Path, manifest: dict[str, Any]) -> dict[str, pd.Series]:
    """Flow by edge for each network run this run wrote, by ``<mode>-<preset>``."""
    found = {}
    for name in sorted(manifest.get("files", {})):
        parts = name.split("/")
        if len(parts) == 3 and parts[0] == "network" and parts[2] == "flows.parquet":
            frame = pd.read_parquet(out / name, columns=["physical_edge_id", "potential_flow"])
            found[parts[1]] = frame.set_index(frame.physical_edge_id.astype(str)).potential_flow
    return found


def validate(
    out: Path,
    counts: gpd.GeoDataFrame,
    *,
    within: BaseGeometry | None = None,
    credit: str,
) -> list[Path]:
    """Match, score and write the validation outputs; return the paths."""
    manifest = json.loads((out / "manifest.json").read_text())
    flows = network_flows(out, manifest)
    if not flows:
        raise AccessibilityError(
            f"{out}: no network flows; add network_flows to the study config and run it again"
        )
    links = gpd.read_parquet(out / "links.parquet")
    sites = match(counts, links, flows, within=within)
    folder = out / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    stored = sites.assign(
        link_ids=sites.link_ids.map(";".join),
        section=sites.section.map(lambda groups: "|".join(";".join(g) for g in groups)),
    )
    paths = [folder / "sites.parquet", folder / "report.md", folder / "report.html"]
    stored.to_parquet(paths[0])
    blocks = report_blocks(sites, manifest, credit, sorted(flows))
    paths[1].write_text(markdown(blocks))
    paths[2].write_text(page(blocks, _title(manifest)))
    return paths


def _title(manifest: dict[str, Any]) -> str:
    study = str(manifest.get("study") or "")
    return f"Modelled flows against counts, {study}" if study else "Modelled flows against counts"


def report_blocks(
    sites: pd.DataFrame, manifest: dict[str, Any], credit: str, runs: list[str]
) -> list[Block]:
    blocks: list[Block] = [Heading(1, _title(manifest))]
    if manifest.get("synthetic"):
        blocks.append(Para(SYNTHETIC))
    blocks += [
        Para(
            "Each count site is matched to the street it is on, and the modelled flow there "
            "is compared with the count by rank: does the model put busy sites above quiet "
            "ones? Flows are potential use from jobs-based demand, not forecasts of counts, "
            "and nothing was fitted to these counts."
        ),
        Heading(2, "Sites"),
        _status_table(sites),
    ]
    for mode in ("walk", "bike"):
        mode_runs = [r for r in runs if r.startswith(f"{mode}-")]
        if not mode_runs or not sites["mode"].eq(mode).any():
            continue
        blocks += _mode_blocks(sites, mode, mode_runs)
    blocks += [
        Heading(2, "How to read this"),
        Bullets(
            (
                "Spearman's ρ runs from −1 to 1; 0 means the model's order of sites is no "
                "better than chance. The interval is a 95% bootstrap over sites.",
                "“Better than shortest” is the run's ρ minus the shortest-distance run's, "
                "on the same resamples: an interval above 0 means the route-choice preset "
                "ranks sites better than plain shortest paths.",
                "The street-class baseline uses no routing: the class of the street a site "
                "is on, plus a step for a bike lane or two for a track or path.",
                "Counts measure today's use, shaped by today's facilities and by trips the "
                "model leaves out (through traffic, recreation, transit access); count "
                "years differ, and sites are where agencies chose to count.",
            )
        ),
        Heading(2, "Data"),
        Bullets((credit, *[str(s) for s in manifest.get("sources", [])])),
    ]
    return blocks


def _status_table(sites: pd.DataFrame) -> Table:
    table = pd.crosstab(sites.status, sites["mode"]).reindex(columns=["walk", "bike"], fill_value=0)
    rows = tuple(
        (str(status), *(f"{int(v):,}" for v in values))
        for status, values in zip(table.index, table.to_numpy(), strict=True)
    )
    return Table(("Status", "Walking sites", "Cycling sites"), rows, (False, True, True))


def _mode_blocks(sites: pd.DataFrame, mode: str, runs: list[str]) -> list[Block]:
    found = compare.scores(sites, mode)
    used = sites[sites.status.eq(MATCHED) & sites["mode"].eq(mode)]
    blocks: list[Block] = [Heading(2, MODE_NAMES[mode])]
    if len(used) < compare.MIN_SITES:
        blocks.append(
            Para(f"Only {len(used)} matched sites: too few for a rank correlation.", note=True)
        )
        return blocks
    rows = tuple(
        (
            score.name,
            f"{score.sites:,}",
            _with_interval(score.rho, score.low, score.high),
            _with_interval(score.difference, score.difference_low, score.difference_high),
            "–" if math.isnan(score.top_share) else f"{100 * score.top_share:.0f}%",
        )
        for score in found
    )
    blocks += [
        Table(
            (
                "Run",
                "Sites",
                "Spearman ρ (95%)",
                "Better than shortest (95%)",
                "Busiest tenth shared",
            ),
            rows,
            (False, True, True, True, True),
        ),  # fmt: skip
        Heading(3, "By the source's facility type"),
        _breakdown(used, runs, "facility", _groups(used.facility)),
        Heading(3, "By count year"),
        _breakdown(used, runs, "year", YEAR_BANDS),
    ]
    blocks += [
        Scatter(run, "Counted per day", "Modelled flow",
                tuple(zip(used.volume.astype(float), used[f"flow:{run}"].astype(float),
                          strict=True)))
        for run in runs
    ]  # fmt: skip
    return blocks


def _groups(labels: pd.Series) -> tuple[tuple[str, ...], ...]:
    counts = labels.fillna("").astype(str).replace("", "(none)").value_counts()
    return tuple((str(label),) for label, n in counts.items() if n >= compare.MIN_SITES)


def _breakdown(
    used: pd.DataFrame, runs: list[str], column: str, groups: tuple[tuple[Any, ...], ...]
) -> Table:
    rows = []
    for group in groups:
        if column == "year":
            low, high, label = group
            year = pd.to_numeric(used.year, errors="coerce")
            part = used[year.between(low, high)]
        else:
            label = group[0]
            part = used[used.facility.fillna("").astype(str).replace("", "(none)").eq(label)]
        if len(part) < compare.MIN_SITES:
            continue
        volume = part.volume.to_numpy(dtype=float)
        rhos = [compare.spearman(volume, part[f"flow:{r}"].to_numpy(dtype=float)) for r in runs]
        rows.append((str(label), f"{len(part):,}", *(_number(r) for r in rhos)))
    if not rows:
        rows.append(("(no group with 10 or more sites)", "", *("" for _ in runs)))
    return Table(("Group", "Sites", *runs), tuple(rows), (False, True, *(True for _ in runs)))


def _number(value: float) -> str:
    return "–" if not np.isfinite(value) else f"{value:.2f}"


def _with_interval(value: float, low: float, high: float) -> str:
    if not np.isfinite(value):
        return "–"
    if not (np.isfinite(low) and np.isfinite(high)):
        return f"{value:.2f}"
    return f"{value:.2f} ({low:.2f} to {high:.2f})"
