"""The settings behind the exposure lists: each factor's risk points, and the fix-first rule.

The defaults are the hand-set values in ``docs/assumptions.md`` ("Where routes
meet traffic today").  A study may change any of them under ``exposure`` in
its config; the run records the values used in its manifest, and the report
and map print them, so a list can always be read against its own rule.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, fields
from typing import Any

LTS_LEVELS = (1, 2, 3, 4)
# Broach's daily-traffic bands (network/broach.py): under 5,000 vehicles a day,
# 5,000-10,000, 10,000-20,000, 20,000-30,000, 30,000 or more.
BANDS = (0, 1, 2, 3, 4)
BAND_WORDS = {
    0: "under 5,000 vehicles a day",
    1: "5,000 to 10,000 vehicles a day",
    2: "10,000 to 20,000 vehicles a day",
    3: "20,000 to 30,000 vehicles a day",
    4: "30,000 or more vehicles a day",
}


@dataclass(frozen=True)
class RiskPoints:
    """The points each risk factor adds; see "Where routes meet traffic today"."""

    # Cycling along a street, by its level of traffic stress (1 to 4).
    bike_lts: dict[int, float] = field(default_factory=lambda: {1: 0, 2: 1, 3: 3, 4: 5})
    # Walking along a street with no known sidewalk, by its traffic band.
    walk_no_sidewalk: dict[int, float] = field(
        default_factory=lambda: {0: 1, 1: 2, 2: 4, 3: 4, 4: 4}
    )
    fast_mph: float = 35.0  # a speed limit at or over this is "fast"
    fast: float = 1.0  # fast, on a street walked without a sidewalk or a road crossed
    drop_off_share: float = 0.10  # the busiest share of streets for car trips here
    drop_off: float = 1.0  # added only where a street already has other points
    # Crossing a road, by its traffic band.
    crossing_band: dict[int, float] = field(default_factory=lambda: {0: 0, 1: 1, 2: 2, 3: 3, 4: 4})
    wide_lanes: float = 4.0  # total lanes at or over which a crossing is "wide"
    wide: float = 1.0
    unmarked: float = 1.0  # walking, no marked crosswalk, on a road of band 1 or more
    signal_factor: float = 0.25  # a signal multiplies a crossing's points by this


@dataclass(frozen=True)
class FixFirst:
    """Which places are "fix first"; every other place with points is "watch"."""

    min_points: float = 3.0
    # And at least this many people, or this share of those counted, whichever is smaller.
    min_people: float = 5.0
    min_share: float = 0.05
    # When set, the busiest share of a list's places (by people) are fix first too,
    # whatever their points, so long as they meet the people bar.
    busiest_share: float | None = None


@dataclass(frozen=True)
class ExposureRules:
    points: RiskPoints = field(default_factory=RiskPoints)
    fix_first: FixFirst = field(default_factory=FixFirst)

    def as_dict(self) -> dict[str, Any]:
        """Plain values for a manifest; :func:`rules_from` reads them back."""
        return asdict(self)


def rules_from(raw: Mapping[str, Any] | None) -> ExposureRules:
    """Rules from a config's or manifest's ``exposure`` mapping; unset values keep their defaults.

    Raises ``ValueError`` naming the setting at fault.
    """
    if raw is None:
        return ExposureRules()
    _known(raw, ("points", "fix_first"), "exposure")
    return ExposureRules(
        points=_points(_mapping(raw.get("points", {}), "exposure.points")),
        fix_first=_fix_first(_mapping(raw.get("fix_first", {}), "exposure.fix_first")),
    )


def fix_first_rule(rule: FixFirst) -> str:
    """The fix-first rule in a sentence, as the report and map print it."""
    text = (
        f"Fix first: {rule.min_points:.12g} or more risk points, and at least "
        f"{rule.min_people:.12g} people (or {percent(rule.min_share)} of those counted, "
        "whichever is smaller)"
    )
    if rule.busiest_share is not None:
        text += (
            f"; or among the busiest {percent(rule.busiest_share)} of the list's places, "
            "with the same people bar, whatever their points"
        )
    return text + "."


def points_rows(points: RiskPoints) -> list[tuple[str, str]]:
    """Each factor and its points, in words, as the report's table lists them."""

    def by(table: Mapping[int, float], levels: tuple[int, ...]) -> str:
        return " / ".join(f"{table[level]:.12g}" for level in levels)

    bands = "; ".join(f"{BAND_WORDS[b]}: {points.walk_no_sidewalk[b]:.12g}" for b in BANDS)
    crossing = "; ".join(f"{BAND_WORDS[b]}: {points.crossing_band[b]:.12g}" for b in BANDS)
    return [
        ("Cycling along a street, by traffic stress level 1 / 2 / 3 / 4",
         by(points.bike_lts, LTS_LEVELS)),
        ("Walking along a street with no known sidewalk, by its traffic", bands),
        (f"{points.fast_mph:.12g} mph or faster (a street walked without a sidewalk, "
         "or a road crossed)", f"+{points.fast:.12g}"),
        (f"Among the busiest {percent(points.drop_off_share)} of streets for car trips to the "
         "destination (drop-off), on a street with other points", f"+{points.drop_off:.12g}"),
        ("Crossing a road, by its traffic", crossing),
        (f"Crossing {points.wide_lanes:.12g} or more lanes", f"+{points.wide:.12g}"),
        ("Walking, no marked crosswalk on the map, on a road of 5,000 or more vehicles a day",
         f"+{points.unmarked:.12g}"),
        ("A signal at the crossing", f"total × {points.signal_factor:.12g}"),
    ]  # fmt: skip


# Settings print with up to 12 significant figures: enough that a bar of 10.00001
# does not read as 10, few enough that 0.07 × 100 reads as 7.


def percent(share: float) -> str:
    return f"{share * 100:.12g}%"


def signal_words(factor: float) -> str:
    """What a signal does to a crossing's points, in words."""
    named = {0.25: "points cut to a quarter", 0.5: "points cut in half", 1.0: "points not reduced"}
    return named.get(factor, f"points multiplied by {factor:.12g}")


def _points(raw: Mapping[str, Any]) -> RiskPoints:
    default = RiskPoints()
    names = [f.name for f in fields(RiskPoints)]
    _known(raw, names, "exposure.points")
    _no_nulls(raw, names, "exposure.points")
    values: dict[str, Any] = {}
    for name in names:
        where = f"exposure.points.{name}"
        given = raw.get(name)
        if name in {"bike_lts", "walk_no_sidewalk", "crossing_band"}:
            levels = LTS_LEVELS if name == "bike_lts" else BANDS
            table = dict(getattr(default, name))
            for key, value in _mapping(given if given is not None else {}, where).items():
                level = _level(key, levels, where)
                table[level] = _amount(value, f"{where}.{level}")
            values[name] = table
        elif given is None:
            values[name] = getattr(default, name)
        elif name in {"fast_mph", "wide_lanes"}:
            values[name] = _amount(given, where, positive=True)
        elif name == "drop_off_share":
            values[name] = _share(given, where)
        else:
            values[name] = _amount(given, where)
    return RiskPoints(**values)


def _fix_first(raw: Mapping[str, Any]) -> FixFirst:
    _known(raw, [f.name for f in fields(FixFirst)], "exposure.fix_first")
    _no_nulls(raw, ["min_points", "min_people", "min_share"], "exposure.fix_first")
    default = FixFirst()
    where = "exposure.fix_first."
    busiest = raw.get("busiest_share")
    return FixFirst(
        min_points=_amount(raw.get("min_points", default.min_points), where + "min_points"),
        min_people=_amount(raw.get("min_people", default.min_people), where + "min_people"),
        min_share=_share(raw.get("min_share", default.min_share), where + "min_share"),
        busiest_share=None
        if busiest is None
        else _share(busiest, where + "busiest_share", positive=True),
    )


def _known(raw: Mapping[str, Any], names: Any, where: str) -> None:
    unknown = sorted(set(map(str, raw)) - set(names))
    if unknown:
        raise ValueError(f"{where}: unknown setting {unknown[0]!r}")


def _no_nulls(raw: Mapping[str, Any], names: Any, where: str) -> None:
    """A setting given as null is a mistake, not a request for the default."""
    for name in names:
        if name in raw and raw[name] is None:
            raise ValueError(
                f"{where}.{name}: expected a value, not null; leave it out for the default"
            )


def _mapping(raw: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{where}: expected a mapping")
    return raw


def _number(raw: Any, where: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw):
        raise ValueError(f"{where}: expected a number")
    # To 12 significant figures, as the lists' totals are and as the report prints.
    return float(f"{float(raw):.12g}")


def _amount(raw: Any, where: str, *, positive: bool = False) -> float:
    value = _number(raw, where)
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{where}: expected a {'positive' if positive else 'non-negative'} number")
    return value


def _share(raw: Any, where: str, *, positive: bool = False) -> float:
    value = _number(raw, where)
    if not 0 <= value <= 1 or (positive and value == 0):
        raise ValueError(f"{where}: expected a share {'above 0' if positive else 'from 0'} to 1")
    return value


def _level(key: Any, levels: tuple[int, ...], where: str) -> int:
    """A level from YAML (a number) or a manifest (text)."""
    try:
        level = int(key) if not isinstance(key, bool) and str(key).strip().isdigit() else None
    except ValueError:
        level = None
    if level not in levels:
        raise ValueError(f"{where}: {key!r} is not one of {', '.join(map(str, levels))}")
    return level


__all__ = [
    "BANDS",
    "BAND_WORDS",
    "LTS_LEVELS",
    "ExposureRules",
    "FixFirst",
    "RiskPoints",
    "fix_first_rule",
    "percent",
    "points_rows",
    "rules_from",
    "signal_words",
]


# Tiers: which places are fix first, and the sentence that says why.


def tidy(value: float) -> float:
    """``value`` to 12 significant figures, as settings print, so decimal settings act as
    written: 0.1 + 0.7 is 0.8 and 7% of 100 is 7, not 0.7999999999999999 and
    7.000000000000001."""
    return float(f"{float(value):.12g}")


def people_bar(rule: FixFirst, counted: float) -> float:
    return tidy(min(rule.min_people, rule.min_share * counted)) if counted > 0 else rule.min_people


def tier_of(
    points: float,
    people: float,
    counted: float,
    rule: FixFirst | None = None,
    busiest: float = math.inf,
) -> str:
    """Fix first or watch.  ``busiest`` is the people at the edge of the busiest share."""
    rule = rule or FixFirst()
    if people < people_bar(rule, counted):
        return "watch"
    return "fix first" if points >= rule.min_points or people >= busiest else "watch"


def tier_reason(
    points: float,
    people: float,
    counted: float,
    rule: FixFirst | None = None,
    busiest: float = math.inf,
    flow: str = "people",
) -> str:
    """One plain sentence on why a place is in its tier.

    ``flow`` names what the place's figure counts, as the run's lists head it:
    "people", the study's own unit, or "expected trips".  Figures are whole
    numbers where that reads true beside the bar, and carry more digits where
    it would not: 4.6 against a bar of 5 is not "5", nor 2.999 points "3".
    """
    rule = rule or FixFirst()
    need = people_bar(rule, counted)
    pts, _ = _against(points, rule.min_points)
    bar = f"{rule.min_points:.12g}"  # a setting reads as set: 3.001 is not "3"
    if points < rule.min_points:
        if people >= need and people >= busiest:
            return (f"Fix first: among the busiest {percent(rule.busiest_share or 0)} of places "
                    f"here ({_use("about " + _about(people), flow)}), though it has {pts} "
                    f"{_points_word(pts)}.")  # fmt: skip
        if people < need:  # short on both: say both
            used, needed = _people_against(people, need, rule)
            text = (f"Watch: it has {pts} {_points_word(pts)} (fix first needs {bar}), and fewer "
                    f"than {_use(needed, flow)} (about {used}{_bar_note(rule, counted)})")  # fmt: skip
        else:
            text = (f"Watch: {_use("about " + _about(people), flow)}, but it has {pts} "
                    f"{_points_word(pts)}; fix first needs {bar}")  # fmt: skip
        if math.isfinite(busiest):
            # Being among the busiest and over the minimum is the same as reaching both.
            used, top = _whole_against(people, max(busiest, need))
            if max(busiest, need) == rule.min_people:
                top = f"{rule.min_people:.12g}"
            text += (f", or about {top} {_noun(top, flow)} or more, which would put it among the busiest "
                     f"{percent(rule.busiest_share or 0)} of places and over the minimum "
                     f"(this has about {used})")  # fmt: skip
        return text + "."
    if people >= need:
        return (f"Fix first: {pts} {_points_word(pts)} (fix first needs {bar}), "
                f"and {_use("about " + _about(people), flow)}.")  # fmt: skip
    used, needed = _people_against(people, need, rule)
    return (f"Watch: {pts} {_points_word(pts)}, but fewer than {_use(needed, flow)} "
            f"(about {used}{_bar_note(rule, counted)}).")  # fmt: skip


def _noun(shown: str, flow: str) -> str:
    """``flow`` for the number shown: exactly one is "person", "resident aged 5-11", "trip"."""
    if shown.split()[-1] != "1":
        return flow
    if flow == "people":
        return "person"
    first, _, rest = flow.partition(" aged ")
    one = first[:-1] if first.endswith("s") else first
    return f"{one} aged {rest}" if rest else one


def _use(shown: str, flow: str) -> str:
    """ "about 12 people use it"; "about 1 person uses it"."""
    noun = _noun(shown, flow)
    return f"{shown} {noun} {'use' if noun == flow else 'uses'} it"


def _people_against(people: float, need: float, rule: FixFirst) -> tuple[str, str]:
    used, needed = _whole_against(people, need)
    return used, f"{rule.min_people:.12g}" if need == rule.min_people else needed


def _bar_note(rule: FixFirst, counted: float) -> str:
    """Which minimum applied, when it is the share and not the plain number."""
    if counted <= 0 or people_bar(rule, counted) >= rule.min_people:
        return ""
    shown = f"{counted:,.0f}" if counted >= 100 else f"{counted:.3g}"
    return f"; the bar is {percent(rule.min_share)} of the {shown} counted"


def busiest_edge(people: Iterable[float], share: float | None) -> float:
    """The value at the edge of the busiest ``share`` of a list; inf when off or empty."""
    top = sorted((float(v) for v in people), reverse=True)
    if not share or not top:
        return math.inf
    # Rounded first: 0.07 × 100 is 7.000000000000001 in floating point, not 8 places.
    return top[max(math.ceil(round(share * len(top), 9)), 1) - 1]


def _points_word(shown: str) -> str:
    return "risk point" if shown == "1" else "risk points"


def _against(value: float, bar: float) -> tuple[str, str]:
    """``value`` and ``bar`` as text, with enough digits to keep them in order."""
    for digits in range(3, 16):
        a, b = f"{value:.{digits}g}", f"{bar:.{digits}g}"
        if (float(a) < float(b)) == (value < bar) and (a == b) == (value == bar):
            return a, b
    return repr(value), repr(bar)


def _whole_against(value: float, bar: float) -> tuple[str, str]:
    """A flow and its bar as whole numbers, or with the decimals it takes to keep them in
    order and a flow above nothing from reading as 0."""
    for places in range(13):
        a, b = round(value, places), round(bar, places)
        if (a < b) == (value < bar) and (a == b) == (value == bar) and (a > 0) == (value > 0):
            return _plain(a, places), _plain(b, places)
    return repr(value), repr(bar)


def _plain(value: float, places: int) -> str:
    shown = f"{value:,.{places}f}"
    return shown.rstrip("0").rstrip(".") if "." in shown else shown


def _about(flow: float) -> str:
    """A flow on its own, as a whole number; a part of one keeps a digit."""
    return f"{flow:,.0f}" if flow >= 0.95 or flow <= 0 else f"{flow:.1g}"
