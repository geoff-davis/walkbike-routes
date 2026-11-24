"""Words and number formats shared by the report and the maps.

Both read the same run, so a figure or a phrase that appears in both is made
here once: a place's people are "12" in the report's table and on the map.
"""

from __future__ import annotations

import functools
import json
import math
import re
from collections.abc import Mapping

from walkbike_routes.analysis.exposure_rules import (
    ExposureRules,
    FixFirst,
    fix_first_rule,
    signal_words,
)

# For a run written before manifests listed their sources.
FALLBACK_SOURCES = [
    "Streets: © OpenStreetMap contributors (ODbL)",
    "Population: US Census Bureau, 2020 Census",
]
SYNTHETIC = "Made-up town: every street, home and person here is invented; nothing is real."
NOT_COUNTS = "Flows show where trips would go, not how many happen: they are not counts."
# Printed at the top of every report and map until the outputs are ready to share.
DRAFT = (
    "Work in progress: this tool and its reports are still being developed and checked. "
    "Treat every figure as a draft."
)

MODES = ("walk", "bike", "car")  # the order modes are listed in, everywhere
MODE_NAMES = {"walk": "Walking", "bike": "Cycling", "car": "Driving"}
MODE_VERBS = {"walk": "walk", "bike": "cycle", "car": "drive"}
NO_DECAY = "none"
FIX_FIRST, WATCH = "fix first", "watch"


def mode_rank(mode: str) -> int:
    """Where a mode sorts: walking, cycling, driving, then any other."""
    return MODES.index(mode) if mode in MODES else len(MODES)


def title(destination: str, study: str) -> str:
    return destination if not study or study == destination else f"{destination}, {study}"


def number(value: float | None, places: int = 0) -> str:
    """A figure with thousands separators; ``–`` when missing; no trailing ``.0``."""
    if value is None or not math.isfinite(value):
        return "–"
    text = f"{value:,.{places}f}"
    return text[:-2] if places == 1 and text.endswith(".0") else text


def people(value: float | None) -> str:
    """A whole number of people; a part of one person is "fewer than 1", not 0."""
    if value is None or not math.isfinite(value):
        return "–"
    if 0 < value < 0.5:
        return "fewer than 1"
    return f"{value:,.0f}"


def points(value: float | None) -> str:
    """Risk points as set, however fine: 0.75 is not "0.8"."""
    return "–" if value is None or not math.isfinite(value) else f"{value:.12g}"


def percent(share: float) -> str:
    """A share as a whole percentage; a sliver is "under 1%", not "0%"."""
    return "under 1%" if 0 < share < 0.01 else f"{share:.0%}"


def length(metres: float) -> str:
    """A street's length: metres to the nearest ten, or kilometres."""
    if metres >= 1000:
        return f"{metres / 1000:.1f} km"
    return f"{max(round(metres, -1), 10):.0f} m"


def minutes(value: float | None) -> str:
    """Minutes to one decimal, kept even when it is ".0": a column of times lines up."""
    return "–" if value is None or not math.isfinite(value) else f"{value:,.1f}"


def capital(text_: str) -> str:
    return text_[:1].upper() + text_[1:]


def unit_words(unit: object) -> str:
    """The run's unit of people as words; "people" when it has none."""
    text_ = str(unit or "").replace("_", " ").strip()
    return text_ if text_ and text_ != "nan" else "people"


def decayed(curve: object) -> bool:
    """Whether a run weights each home by a distance-decay curve."""
    return str(curve or "") not in ("", NO_DECAY, "nan", "None")


def flow_heading(unit: str, is_decayed: bool) -> str:
    """The heading over a place's flow: who is counted there."""
    return "Expected trips" if is_decayed else capital(unit)


def flow_noun(unit: str, is_decayed: bool) -> str:
    """What a place's flow counts, as the noun in its sentence: "about 12 <flow_noun> use it"."""
    return "expected trips" if is_decayed else unit


def flow_words(unit: str, is_decayed: bool) -> str:
    """What a place's flow counts, as a phrase: "12 <flow_words>"."""
    return f"expected trips by {unit}" if is_decayed else unit


def from_each(value: object, labels: Mapping[str, str]) -> str:
    """Each destination's people at a place and its share: "Egan 12 (60%); Almond 8 (40%)"."""
    if not isinstance(value, str) or not value:
        return ""
    found = {str(d): float(v) for d, v in json.loads(value).items()}
    total = sum(found.values())
    if total <= 0:
        return ""
    if len(found) == 1:  # all from one place: its name says so
        only = next(iter(found))
        return labels.get(only, only)
    return "; ".join(
        f"{labels.get(d, d)} {people(v)} ({percent(v / total)})" for d, v in found.items()
    )


def tag(kind: str, rank: int) -> str:
    """A place's short name, the same on the map and in the report: S3, C1."""
    return f"{'S' if kind.startswith('street') else 'C'}{rank}"


def how_to_read(rules: ExposureRules) -> str:
    """How the exposure lists are made and tiered, with the run's own rule; printed once."""
    return (
        "Where a walking or cycling route runs along or across traffic, the place gets risk "
        "points for each factor it has: traffic, speed, lanes, a missing sidewalk, an unmarked "
        "crossing, and drop-off traffic. A signal at a crossing: "
        f"{signal_words(rules.points.signal_factor)}. Places are ranked by flow × risk points, "
        f"fix-first places first. {fix_first_rule(rules.fix_first)} Every other place with "
        "points is “watch”. Points come from the systemic safety guides and the level "
        "of traffic stress; they are not crash rates, and flows are potential, not counts."
    )


# The older name, used by the maps' data.
exposure_note = how_to_read


def standard_reason(rule: FixFirst) -> bool:
    """Whether every fix-first place is one for the same reason: its points and its people."""
    return rule.busiest_share is None


def listed(names: list[str]) -> str:
    """Names in a sentence: "a", "a and b", "a, b and c"."""
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def count(n: int, one: str, many: str | None = None) -> str:
    """A count with its noun: "1 street", "3 streets"."""
    return f"{n:,} {one if n == 1 else many or one + 's'}"


ROUTE_COST_SAVED = "Route-cost min saved"
REPO = "https://github.com/geoff-davis/walkbike-routes"
METHODS_DOC = f"{REPO}/blob/main/docs/routing-methods.md"
ROUTE_COST_DOC = f"{METHODS_DOC}#route-cost-minutes"
# Each shipped route model's section of the methods page, by mode and name;
# "*" stands for any name of the mode not listed.
MODEL_SECTIONS = {
    "walk/broach-2016": "walking-broach-2016",
    "walk/broach-2016-child": "walking-broach-2016",
    "walk/broach-2016-sidewalks": "walking-broach-2016",
    "walk/shortest": "walking-shortest-distance-walkshortest",
    "bike/broach-2011": "cycling-broach-gliebe-and-dill-2011",
    "bike/broach-2011-child": "cycling-broach-gliebe-and-dill-2011",
    "bike/broach-2011-lanes": "cycling-broach-gliebe-and-dill-2011",
    "bike/broach-2011-commute": "cycling-broach-gliebe-and-dill-2011",
    "bike/furth": "cycling-furths-level-of-traffic-stress-bikefurth",
    "bike/shortest": "cycling-shortest-distance-bikeshortest",
    "car/*": "driving-car",
}


def model_doc(mode: str, name: str) -> str:
    """Where to read how a route model works: its section, or the methods page."""
    section = MODEL_SECTIONS.get(f"{mode}/{name}") or MODEL_SECTIONS.get(f"{mode}/*")
    return f"{METHODS_DOC}#{section}" if section else METHODS_DOC


ROUTE_COST = (
    "Route cost is what the route model keeps low: travel time plus extra minutes for "
    "traffic stress, crossings and turns (often called generalized cost)"
)
UNCONNECTED = (
    "homes with no route on the map: for example, on a one-way street the map does not lead out of"
)


def key_terms(
    rules: ExposureRules, any_decayed: bool, route_cost: bool = False
) -> list[tuple[str, str]]:
    """The few terms a reader needs before the lists, with the run's own rule."""
    rule = fix_first_rule(rules.fix_first).removeprefix("Fix first: ")
    terms = [
        (
            "Risk points",
            "A score for how exposed to traffic a street or crossing on a route is. Each "
            "factor adds points: traffic, speed, lanes to cross, a missing sidewalk, an "
            "unmarked crossing, cycling stress, drop-off traffic. A signal at a crossing: "
            f"{signal_words(rules.points.signal_factor)}. The values are under “Risk "
            "points used”; they are not crash rates.",
        )
    ]
    if any_decayed:
        terms.append(
            (
                "Expected trips",
                "How many trips the model expects on a street or crossing. Each home counts "
                "at the usual share of trips of its length, so the figure is lower than the "
                "number who can get there. A street's figure is at its busiest point. It "
                "shows where trips would go; it is not a count.",
            )
        )
    terms += [
        ("Fix first", f"A place with {rule} These are the places this report lists first."),
        ("Watch", "Every other place with risk points."),
        (
            "S1, C1",
            "A street (S) or crossing (C), numbered by flow × risk points, highest first: "
            "fix-first places first, then the rest. The map uses the same labels.",
        ),
    ]
    if route_cost:
        terms.append(
            (
                "Route-cost minutes",
                "What the route model keeps low when it picks a route: clock minutes plus "
                "extra minutes for what people avoid, such as busy traffic, hills, crossings "
                "and turns. A stressful street can cost more route-cost minutes than a longer "
                "calm one. Barriers are ranked by the route-cost minutes they save. Also "
                "called generalized cost.",
            )
        )
    return terms


FLOW_MEANING = (
    "How many people's routes use each street, if everyone within reach made the trip: "
    "potential flow, not a count."
)
MODEL_RULES = (
    "The rules used to pick each route: how much extra each preset charges for busy "
    "streets, hills, crossings and turns. Each has its source in Method."
)
# Terms readers may not know, defined on hover wherever they appear in report
# or map text (phrases match whole words, ignoring case; keys are lower case).
GLOSSARY: dict[str, str] = {
    "traffic stress": "Level of traffic stress (LTS): how stressful a street is to cycle "
    "on, from 1 (calm enough for children) to 4 (only for confident adults), judged from "
    "its speed limit, lanes, traffic and bike lanes.",
    "risk points": "How exposed to traffic a place is: points for traffic, speed, lanes "
    "to cross, a missing sidewalk or crosswalk, cycling stress and drop-off traffic, added "
    "up. Not a crash rate.",
    "drop-off": "Car trips to the destination, such as parents dropping children at "
    "school. The streets busiest with these trips get extra points; Risk points used "
    "gives the rule.",
    "potential flow": "How many people would use a street if everyone within reach made "
    "the trip by the route the model picks. Not a count of people seen there.",
    "route-cost minutes": ROUTE_COST.replace(" (often called generalized cost)", "")
    + ", in minutes. Also called generalized cost.",
    "generalized cost": ROUTE_COST.replace(" (often called generalized cost)", "") + ".",
    "route model": MODEL_RULES,
    "route-choice model": MODEL_RULES,
    "uncalibrated": "Not fitted to observed trips by the people or in the place studied. "
    "The values may come from published studies or be set by hand; Method explains each "
    "route model.",
    "distance-decay": "How fast trips fall off with distance: the national share of "
    "trips of each length, used to weight homes by how far away they are.",
    "watch": "A place with risk points that does not meet the fix-first threshold.",
    "flow": FLOW_MEANING,
    "flows": FLOW_MEANING,
    "path-size logit": "A way of sharing each home's trips among several near-best "
    "routes, so routes that overlap a lot are not counted as separate choices.",
    "catchment": "The homes counted for a destination: everyone within the time limit, "
    "or every home connected to it.",
    "decayed": "Weighted by the national share of trips of that length, so far-away "
    "homes count for less.",
    "turn-aware": "Counting the time lost at each turn as well as along each street.",
    "osrm": "Open Source Routing Machine, a widely used routing engine whose car turn "
    "delays these values follow.",
    "trpmiles": "The survey's trip length in miles, measured along the routed path.",
    "stress_scale": "How many times the published traffic and crossing penalties are "
    "multiplied; 2 doubles them.",
}
GLOSS_PATTERN = re.compile(
    r"(?<![\w-])("
    + "|".join(re.escape(t) for t in sorted(GLOSSARY, key=len, reverse=True))
    + r")(?![\w-])",
    re.IGNORECASE,
)


@functools.cache
def model_tip(mode: str, name: str) -> str:
    """What a shipped route model is, for a hover note; "" if it is not shipped."""
    from walkbike_routes.costs import presets  # here, so text stays light to import

    try:
        return f"Route model: {presets.load(mode, name).title}."
    except presets.PresetError:
        return ""


def tips(
    header: tuple[str, ...], flow: str = "", models: Mapping[str, str] | None = None
) -> tuple[str, ...]:
    """What each column of a table means, for the HTML report's hover notes; "" for none.

    ``models`` gives the notes for columns named after route models.
    """
    counted = flow.lower() or "people"
    known = {
        "On the map": "The place's label on the map: S for a street, C for a crossing, "
        "numbered by rank. The link opens the map there.",
        "Risk points": "How exposed to traffic the place is: the sum of the factors in "
        "the next column. Not a crash rate.",
        "Length": "The length of the stretch with these risk points.",
        "From each destination": "How many of the place's trips go to each destination. "
        "The place is judged on the total.",
        "Priority": "Fix first or watch, and why.",
        "Time limit": "The longest trip counted for this mode.",
        "Can get there": f"Those whose route is within the time limit. Left out: {UNCONNECTED}.",
        "Share": "Can get there, as a share of everyone in the study area.",
        "Median minutes": "The middle travel time among those who can get there.",
        "Expected trips": "Trips the model expects: each home counts at the usual share "
        "of trips of its length. Not a count.",
        "Change": "The kind of change tried at this place, on its own.",
        "Share of those counted": f"Helped, as a share of all the {counted} to this "
        "destination by this mode.",
        ROUTE_COST_SAVED: "The drop in route cost, added up over everyone helped, in "
        f"minutes. {ROUTE_COST}, so it can fall with no change in clock time. Places are "
        "ranked by it within each kind of change.",
        "Peak flow": "The flow on the street's busiest link.",
        "Street": "A stretch of one street, named by the streets at each end.",
        "Crossing": "Where routes cross a road: all its pieces together, named after the "
        "road crossed and the nearest other street.",
        "Why it has these points": "The factors behind the risk points, each with the "
        "points it adds.",
        "Place": "The street, crossing or junction where the change would go.",
        "Route model": GLOSSARY["route model"],
        "In study area": "Everyone of the counted age in the study area, whether or not "
        "they can get there.",
        "Factor": "What earns a place risk points.",
        "Points": "The risk points the factor adds.",
        "Mode": "How people travel: walking, cycling or driving.",
        **(models or {}),
    }
    for h in header:
        if m := re.fullmatch(r"≤ (.+) min", h):
            known[h] = f"Those whose trip takes {m.group(1)} minutes or less."
    if flow:
        known.setdefault(
            flow, f"The {counted} whose routes use the place, at its busiest point. Not a count."
        )
        known[f"{flow} helped"] = (
            f"The {counted} whose route gets cheaper with the change: shorter, calmer or "
            "with easier crossings. Many would use this place only once it is changed."
        )
    return tuple(known.get(h, "") for h in header)
