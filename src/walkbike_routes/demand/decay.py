"""Distance decay: fewer people walk or cycle the farther they have to go.

A decay curve gives, for a route length, the share of a mode's trips for a
purpose that are at least that long, from a travel survey.  It is 1 at zero
and falls with distance.  A home's weight is multiplied by the curve at the
length of its route, so a home next door counts in full and one at the edge
of reach counts a little.  The curve is the observed share itself, not a
fitted function, so it carries the survey's sampling noise at long distances.

The curves shipped in ``walkbike_routes/decay/`` come from the 2017 National
Household Travel Survey; ``scripts/nhts_decay_curves.py`` rebuilds them.  A
curve is read as a table of (miles, share) points with straight lines
between them; beyond the last point the last share holds.

The weights are what flows would be if trip lengths followed the national
pattern.  They are not counts: they say nothing about how many people make
the trip at all, or by which mode.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources

import numpy as np
import pandas as pd
import yaml

from walkbike_routes.exceptions import AccessibilityError

METRES_PER_MILE = 1609.344
FIELDS = ("name", "mode", "title", "source", "trips", "median_miles", "miles", "share")


class DecayError(AccessibilityError):
    """A decay curve that is missing or malformed."""


@dataclass(frozen=True)
class DecayCurve:
    """A named, cited share of trips at least each distance long."""

    name: str
    mode: str
    title: str
    source: str
    trips: int
    median_miles: float
    miles: tuple[float, ...]
    share: tuple[float, ...]

    def __post_init__(self) -> None:
        where = f"decay curve {self.name!r}"
        if len(self.miles) != len(self.share) or len(self.miles) < 2:
            raise DecayError(f"{where}: needs matching miles and share lists of 2 or more")
        if not all(math.isfinite(v) for v in (*self.miles, *self.share)):
            raise DecayError(f"{where}: miles and share must be finite numbers")
        if self.miles[0] != 0 or self.share[0] != 1:
            raise DecayError(f"{where}: must start at 0 miles with a share of 1")
        if any(b <= a for a, b in zip(self.miles, self.miles[1:], strict=False)):
            raise DecayError(f"{where}: miles must increase")
        if any(b > a for a, b in zip(self.share, self.share[1:], strict=False)):
            raise DecayError(f"{where}: share must not increase with distance")
        if self.share[-1] < 0:
            raise DecayError(f"{where}: share must not be negative")

    def factor(self, length_m: Sequence[float] | np.ndarray | pd.Series) -> np.ndarray:
        """The share at each route length in metres; 0 where the length is unknown."""
        miles = np.asarray(length_m, dtype=float) / METRES_PER_MILE
        out = np.interp(miles, self.miles, self.share)
        return np.where(np.isfinite(miles), out, 0.0)


def names(mode: str | None = None) -> tuple[str, ...]:
    """The shipped curves, optionally only those for one mode."""
    folder = resources.files("walkbike_routes.decay")
    found = sorted(
        p.name.removesuffix(".yaml") for p in folder.iterdir() if p.name.endswith(".yaml")
    )
    return tuple(n for n in found if mode is None or load(n).mode == mode)


def load(name: str) -> DecayCurve:
    """A shipped curve by name."""
    if not name.replace("-", "").isalnum():
        raise DecayError(f"no decay curve named {name!r}")
    path = resources.files("walkbike_routes.decay").joinpath(f"{name}.yaml")
    if not path.is_file():
        raise DecayError(f"no decay curve named {name!r}")
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict) or sorted(raw) != sorted(FIELDS):
        raise DecayError(f"decay curve {name!r}: expected the fields {', '.join(FIELDS)}")
    return DecayCurve(
        name=str(raw["name"]),
        mode=str(raw["mode"]),
        title=str(raw["title"]),
        source=str(raw["source"]),
        trips=int(raw["trips"]),
        median_miles=float(raw["median_miles"]),
        miles=tuple(float(v) for v in raw["miles"]),
        share=tuple(float(v) for v in raw["share"]),
    )


def apply(routes: pd.DataFrame, curve: DecayCurve | None) -> pd.DataFrame:
    """Routes with ``decay_factor`` and ``expected_weight`` columns added.

    The length is the route's network length plus the walk or ride from the
    home to the network.  Homes past the time limit, or with no route, get no
    expected weight; without a curve every home within the limit counts in full.
    """
    out = routes.copy()
    length = out.physical_length_m.astype(float) + out.snap_distance_m.astype(float).fillna(0.0)
    if curve is None:
        factor = np.where(out.route_reachable.astype(bool), 1.0, 0.0)
    else:
        factor = curve.factor(length)
    used = out.route_reachable.astype(bool) & out.within_time_limit.astype(bool)
    out["decay_factor"] = factor
    out["expected_weight"] = np.where(used, out.demand_weight.astype(float) * factor, 0.0)
    return out


def survival(
    miles: Sequence[float] | np.ndarray | pd.Series,
    weights: Sequence[float] | np.ndarray | pd.Series,
    at: Sequence[float],
) -> tuple[float, ...]:
    """Weighted share of trips at least each distance in ``at`` long."""
    length = np.asarray(miles, dtype=float)
    weight = np.asarray(weights, dtype=float)
    keep = np.isfinite(length) & (length >= 0) & np.isfinite(weight) & (weight > 0)
    length, weight = length[keep], weight[keep]
    if weight.sum() <= 0:
        raise DecayError("no trips with a known length and a positive weight")
    return tuple(float(weight[length >= d].sum() / weight.sum()) for d in at)


def weighted_median(
    miles: Sequence[float] | np.ndarray | pd.Series,
    weights: Sequence[float] | np.ndarray | pd.Series,
) -> float:
    """The length half of the weighted trips are shorter than."""
    length = np.asarray(miles, dtype=float)
    weight = np.asarray(weights, dtype=float)
    keep = np.isfinite(length) & (length >= 0) & np.isfinite(weight) & (weight > 0)
    order = np.argsort(length[keep])
    cumulative = np.cumsum(weight[keep][order])
    if cumulative.size == 0:
        raise DecayError("no trips with a known length and a positive weight")
    return float(length[keep][order][np.searchsorted(cumulative, cumulative[-1] / 2)])
