"""Grades on links from node elevations, and walking speed on hills.

A link's grade is its rise from ``u`` to ``v`` over its length, from the
ground elevation at its two end nodes (USGS 3DEP).  Two cases get no grade,
because the end-node heights do not describe the walk:

- bridges and tunnels, where the ground model gives the ground under the
  bridge or over the tunnel, and links meeting their ends, since the
  bare-earth model can put a bridge's end below its deck;
- links shorter than ``MIN_LENGTH_M``, where a few tenths of a metre of
  elevation error is a steep false grade.

Grades are clipped to ``GRADE_LIMIT``; steeper values are elevation errors.
A link's own shape between its ends is not sampled, so a link over a crest
counts only its net rise.

Walking speed on a grade follows Tobler's hiking function (Tobler 1993),
``6 exp(-3.5 |g + 0.05|)`` km/h, used as a ratio to its value on the flat:
walkers are fastest on a gentle descent of about 5% and slow on steeper
slopes either way.  It was fitted to hiking data, not city walking.

Cycling speed on a grade follows Parkin and Rotheram (2010, Table 2), a
linear fit to 518 speeds of 16 experienced commuters in Leeds, measured at
the end of each start from rest: 6.01 m/s on the flat, 0.2379 m/s faster per
1% down and 0.4002 m/s slower per 1% up.  It too is used as a ratio to its
flat value, and grades are held to the range the data covered (8.39% down
to 9.34% up), since the straight line reaches zero speed at 15% up.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MIN_LENGTH_M = 10.0
GRADE_LIMIT = 0.30
STRUCTURES = frozenset({"bridge", "tunnel"})


def link_grades(
    u: pd.Series,
    v: pd.Series,
    length_m: pd.Series,
    structure: pd.Series | None,
    elevation_by_node: pd.Series,
) -> np.ndarray:
    """The grade from ``u`` to ``v`` of each link; NaN where it is not known."""
    start = u.astype(str).map(elevation_by_node).to_numpy(float)
    end = v.astype(str).map(elevation_by_node).to_numpy(float)
    rise = end - start
    length = length_m.to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        grade = np.clip(rise / length, -GRADE_LIMIT, GRADE_LIMIT)
    unknown = ~(length >= MIN_LENGTH_M)
    if structure is not None:
        on_structure = structure.astype(str).str.lower().isin(STRUCTURES).to_numpy()
        ends = set(u.astype(str)[on_structure]) | set(v.astype(str)[on_structure])
        unknown |= on_structure | u.astype(str).isin(ends).to_numpy()
        unknown |= v.astype(str).isin(ends).to_numpy()
    return np.where(unknown, np.nan, grade)


def tobler_factor(grade: float) -> float:
    """Walking speed on ``grade`` as a share of walking speed on the flat (1 if unknown)."""
    if not np.isfinite(grade):
        return 1.0
    return float(np.exp(-3.5 * (abs(grade + 0.05) - 0.05)))


PARKIN_FLAT_MPS = 6.01
PARKIN_DOWNHILL_MPS = 23.79  # per unit of downhill grade (0.2379 m/s per 1%)
PARKIN_UPHILL_MPS = 40.02  # per unit of uphill grade (0.4002 m/s per 1%)
PARKIN_GRADES = (-0.0839, 0.0934)  # the steepest grades in the data


def parkin_factor(grade: float) -> float:
    """Cycling speed on ``grade`` as a share of cycling speed on the flat (1 if unknown)."""
    if not np.isfinite(grade):
        return 1.0
    g = min(max(grade, PARKIN_GRADES[0]), PARKIN_GRADES[1])
    speed = PARKIN_FLAT_MPS - PARKIN_DOWNHILL_MPS * min(g, 0.0) - PARKIN_UPHILL_MPS * max(g, 0.0)
    return speed / PARKIN_FLAT_MPS
