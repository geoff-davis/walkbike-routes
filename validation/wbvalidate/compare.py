"""How well modelled flows rank count sites: rank correlation, with uncertainty.

Flows are potential use under assumed demand, not counts, so only ranks are
compared: does the model put busy sites above quiet ones?  For each run, over
the matched sites of its mode:

- **Spearman's rank correlation** with a 95% bootstrap interval (sites drawn
  with replacement, a fixed seed, percentile interval);
- **the difference from a baseline run** (shortest distance), on the same
  resamples, so the interval says whether the preset ranks sites better;
- **the share of the busiest tenth of sites by count that the model also
  puts in its busiest tenth.**

The street-class baseline scores each site by the class of the street it is
on, plus a step for a bike lane and two for a track or path when cycling, with
no routing at all.  A route-choice model that cannot beat it adds little.

Nothing here is fitted to the counts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from wbvalidate.match import MATCHED, RANK

SEED = 1
DRAWS = 2000
MIN_SITES = 10
BASELINES = {"walk": "walk-shortest", "bike": "bike-shortest"}
FACILITY_STEP = {"lane": 1, "boulevard": 1, "track": 2, "path": 2}


@dataclass(frozen=True)
class Score:
    """One run's (or baseline's) agreement with the counts."""

    name: str
    sites: int
    rho: float
    low: float
    high: float
    top_share: float
    # Paired difference from the shortest-distance run, when there is one.
    difference: float = math.nan
    difference_low: float = math.nan
    difference_high: float = math.nan


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman's rho, with ties given their mean rank; NaN when either side is constant."""
    if len(x) < 2 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return math.nan
    return float(stats.spearmanr(x, y).statistic)


def class_score(sites: pd.DataFrame, mode: str) -> pd.Series:
    """The street-class baseline: road class rank, plus a step for bike facilities."""
    rank = sites.highway.fillna("").astype(str).str.removesuffix("_link").map(RANK).fillna(0)
    if mode == "bike":
        rank = rank + sites.bike_facility.fillna("").astype(str).map(FACILITY_STEP).fillna(0)
    return rank.astype(float)


def scores(sites: pd.DataFrame, mode: str, *, seed: int = SEED, draws: int = DRAWS) -> list[Score]:
    """Every run of ``mode`` in ``sites`` (its ``flow:<run>`` columns), then the class baseline."""
    used = sites[sites.status.eq(MATCHED) & sites["mode"].eq(mode)].reset_index(drop=True)
    volume = used.volume.to_numpy(dtype=float)
    columns = {c.split(":", 1)[1]: used[c].to_numpy(dtype=float)
               for c in used.columns if c.startswith(f"flow:{mode}-")}  # fmt: skip
    columns["street class"] = class_score(used, mode).to_numpy()
    rng = np.random.default_rng(seed)
    samples = rng.integers(0, len(used), size=(draws, len(used))) if len(used) else None
    base = columns.get(BASELINES[mode])
    found = []
    for name, model in columns.items():
        rho = spearman(volume, model)
        low = high = math.nan
        diff = diff_low = diff_high = math.nan
        if samples is not None and len(used) >= MIN_SITES:
            boot = np.array([spearman(volume[s], model[s]) for s in samples])
            low, high = interval(boot)
            if base is not None and name != BASELINES[mode]:
                base_boot = np.array([spearman(volume[s], base[s]) for s in samples])
                diff = rho - spearman(volume, base)
                diff_low, diff_high = interval(boot - base_boot)
        found.append(
            Score(name, len(used), rho, low, high, top_share(volume, model), diff, diff_low,
                  diff_high)
        )  # fmt: skip
    return found


def top_share(volume: np.ndarray, model: np.ndarray, share: float = 0.1) -> float:
    """Of the busiest ``share`` of sites by count, the fraction also in the model's busiest.

    Ties at the cut are shared out: if 3 sites tie for the last 2 places,
    each is in with chance 2/3, so a model with every flow equal scores what
    chance would.
    """
    if len(volume) < 2:
        return math.nan
    count = max(1, int(round(share * len(volume))))
    overlap = float(np.sum(_in_top(volume, count) * _in_top(model, count)))
    return overlap / count


def _in_top(values: np.ndarray, count: int) -> np.ndarray:
    """Each value's chance of being among the ``count`` largest, ties broken at random."""
    cut = np.sort(values)[::-1][count - 1]
    above = values > cut
    tied = values == cut
    return np.asarray(above + tied * (count - above.sum()) / tied.sum(), dtype=float)


def interval(values: np.ndarray) -> tuple[float, float]:
    kept = values[np.isfinite(values)]
    if len(kept) < len(values) / 2:
        return math.nan, math.nan
    low, high = np.percentile(kept, [2.5, 97.5])
    return float(low), float(high)
