"""Rebuild the shipped distance-decay curves from the 2017 NHTS trip file.

Usage::

    uv run python scripts/nhts_decay_curves.py path/to/trippub.csv

``trippub.csv`` is in the public CSV download of the 2017 National Household
Travel Survey, version 1.2 (https://nhts.ornl.gov, Downloads, 2017,
"CSV"). The curves are written to ``src/walkbike_routes/decay/``.

Each curve is the weighted share (trip weight ``WTTRDFIN``) of a mode's trips
for a purpose that are at least each distance long. Distance is ``TRPMILES``,
which the survey derives from the Google Maps route between the trip ends.
Loop trips (same start and end, ``LOOP_TRIP`` = 1), whose distance is
reported rather than routed, are left out, as are trips with no distance or
age.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yaml

from walkbike_routes.demand.decay import survival, weighted_median

OUT = Path(__file__).resolve().parents[1] / "src" / "walkbike_routes" / "decay"
MILES = (0, 0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10, 15, 20)
WALK, BIKE = 1, 2  # TRPTRANS
SCHOOL, WORK = 8, 3  # WHYTO
EVERYDAY = (11, 12, 13, 14, 15, 17)  # goods, services, meals, errands, recreation, visits
SOURCE = (
    "Federal Highway Administration, 2017 National Household Travel Survey, "
    "version 1.2, public trip file (https://nhts.ornl.gov). Weighted share of "
    "{what} at least each distance long, by the survey's routed trip distance "
    "(TRPMILES), loop trips excluded. Built by scripts/nhts_decay_curves.py."
)

# name, mode code, title, description of the trips, filter
CURVES = [
    ("walk-everyday", WALK, "Walking for everyday errands, ages 16 and over",
     "walking trips by people 16 and over to shop, buy services or meals, run "
     "errands, for recreation or to visit friends",
     lambda t: t.WHYTO.isin(EVERYDAY) & t.R_AGE.ge(16)),
    ("bike-everyday", BIKE, "Cycling for everyday errands, ages 16 and over",
     "cycling trips by people 16 and over to shop, buy services or meals, run "
     "errands, for recreation or to visit friends",
     lambda t: t.WHYTO.isin(EVERYDAY) & t.R_AGE.ge(16)),
    ("walk-work", WALK, "Walking to work", "walking trips to work",
     lambda t: t.WHYTO.eq(WORK)),
    ("bike-work", BIKE, "Cycling to work", "cycling trips to work",
     lambda t: t.WHYTO.eq(WORK)),
    ("walk-school-5-10", WALK, "Walking to school, ages 5 to 10",
     "walking trips to school by students aged 5 to 10",
     lambda t: t.WHYTO.eq(SCHOOL) & t.R_AGE.between(5, 10)),
    ("walk-school-11-13", WALK, "Walking to school, ages 11 to 13",
     "walking trips to school by students aged 11 to 13",
     lambda t: t.WHYTO.eq(SCHOOL) & t.R_AGE.between(11, 13)),
    ("walk-school-14-17", WALK, "Walking to school, ages 14 to 17",
     "walking trips to school by students aged 14 to 17",
     lambda t: t.WHYTO.eq(SCHOOL) & t.R_AGE.between(14, 17)),
    ("walk-school-5-17", WALK, "Walking to school, ages 5 to 17",
     "walking trips to school by students aged 5 to 17",
     lambda t: t.WHYTO.eq(SCHOOL) & t.R_AGE.between(5, 17)),
    # Too few cycling trips to school (56 to 95) to split by age.
    ("bike-school-5-17", BIKE, "Cycling to school, ages 5 to 17",
     "cycling trips to school by students aged 5 to 17 (too few to split by age)",
     lambda t: t.WHYTO.eq(SCHOOL) & t.R_AGE.between(5, 17)),
]  # fmt: skip


def main(path: str) -> None:
    columns = ["TRPTRANS", "WHYTO", "TRPMILES", "R_AGE", "WTTRDFIN", "LOOP_TRIP"]
    trips = pd.read_csv(path, usecols=columns)
    trips = trips[trips.TRPMILES.ge(0) & trips.R_AGE.ge(0) & trips.LOOP_TRIP.ne(1)]
    for name, mode, title, what, keep in CURVES:
        chosen = trips[trips.TRPTRANS.eq(mode) & keep(trips)]
        curve = {
            "name": name,
            "mode": "walk" if mode == WALK else "bike",
            "title": title,
            "source": SOURCE.format(what=what),
            "trips": len(chosen),
            "median_miles": round(weighted_median(chosen.TRPMILES, chosen.WTTRDFIN), 3),
            "miles": list(MILES),
            "share": [round(s, 4) for s in survival(chosen.TRPMILES, chosen.WTTRDFIN, MILES)],
        }
        text = yaml.safe_dump(curve, sort_keys=False, width=88, default_flow_style=None)
        (OUT / f"{name}.yaml").write_text(text)
        print(f"{name}: {len(chosen)} trips, median {curve['median_miles']} mi")


if __name__ == "__main__":
    main(sys.argv[1])
