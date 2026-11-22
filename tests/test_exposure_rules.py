"""The exposure settings: risk points and the fix-first rule, from a study config."""

from __future__ import annotations

import json
import math

import pandas as pd
import pytest

from tests.test_config import changed
from tests.test_exposure import _edges, _nodes, _shares
from walkbike_routes import config
from walkbike_routes.analysis import exposure, exposure_crossings, exposure_lists, exposure_rules
from walkbike_routes.analysis.exposure_rules import (
    ExposureRules,
    FixFirst,
    RiskPoints,
    fix_first_rule,
    points_rows,
    rules_from,
)


def test_unset_values_keep_their_defaults() -> None:
    assert rules_from(None) == ExposureRules() == rules_from({})
    rules = rules_from({"points": {"bike_lts": {3: 4}}, "fix_first": {"busiest_share": 0.1}})
    assert rules.points.bike_lts == {1: 0, 2: 1, 3: 4, 4: 5}
    assert rules.points.fast == 1 and rules.fix_first.min_points == 3
    assert rules.fix_first.busiest_share == 0.1


def test_the_manifest_copy_reads_back_the_same() -> None:
    rules = rules_from({"points": {"walk_no_sidewalk": {0: 2}}, "fix_first": {"min_people": 8}})
    assert rules_from(json.loads(json.dumps(rules.as_dict()))) == rules


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"weights": {}}, "exposure: unknown setting 'weights'"),
        ({"points": {"bike_lts": {5: 1}}}, r"exposure.points.bike_lts: 5 is not one of 1, 2, 3, 4"),
        ({"points": {"crossing_band": {"x": 1}}}, "crossing_band: 'x' is not one of"),
        ({"points": {"fast": -1}}, "exposure.points.fast: expected a non-negative number"),
        ({"points": {"fast_mph": 0}}, "fast_mph: expected a positive number"),
        ({"points": {"wide": True}}, "exposure.points.wide: expected a number"),
        ({"points": {"drop_off_share": 1.5}}, "drop_off_share: expected a share from 0 to 1"),
        ({"fix_first": {"busiest_share": 0}}, "busiest_share: expected a share above 0 to 1"),
        ({"fix_first": {"min_share": -0.1}}, "min_share: expected a share"),
        ({"fix_first": "lots"}, "exposure.fix_first: expected a mapping"),
    ],
)
def test_bad_settings_are_refused_by_name(raw: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        rules_from(raw)


def test_a_study_config_sets_them() -> None:
    study = config.parse(changed("exposure", {"fix_first": {"min_points": 4}}))
    assert study.exposure.fix_first.min_points == 4
    assert config.parse(changed("exposure", None)).exposure == ExposureRules()
    with pytest.raises(config.ConfigError, match="exposure.points: unknown setting 'speed'"):
        config.parse(changed("exposure", {"points": {"speed": 1}}))


def test_changed_points_change_the_scores_and_their_words() -> None:
    points = RiskPoints(bike_lts={1: 0, 2: 1, 3: 4, 4: 5}, fast_mph=30, signal_factor=0.5)
    risk = exposure.street_risk(_edges(), "bike", points=points)
    assert risk.risk_points["mw"] == 4
    score, text = exposure_crossings._crossing_points(
        2, 30, 2, mode="bike", unmarked=False, signal=True, points=points
    )
    assert score == 1.5  # (2 + 1) × 0.5
    assert "30 mph or faster (+1)" in text and "points cut in half" in text


def test_drop_off_share_sets_which_streets_and_says_so() -> None:
    cars = pd.Series({"mw": 9.0, "me": 5.0, "on": 1.0, "os": 1.0})
    half = exposure.street_risk(_edges(), "bike", cars, RiskPoints(drop_off_share=0.5))
    assert "busiest 50% of streets" in half.risk_factors["me"]
    none = exposure.street_risk(_edges(), "bike", cars, RiskPoints(drop_off_share=0))
    assert not none.risk_factors.str.contains("drop-off").any()


def test_the_busiest_share_makes_a_busy_place_fix_first_whatever_its_points() -> None:
    rule = FixFirst(busiest_share=0.5)
    # Two places, 2 points each: the busier one is in the busiest half.
    table = pd.DataFrame(
        {"place_id": ["a", "b"], "people": [100.0, 10.0], "risk_points": [2.0, 2.0],
         "exposure": [200.0, 20.0], "geometry": [None, None]}
    )  # fmt: skip
    ranked = exposure_lists.ranked(table, [*table.columns, "rank", "tier", "tier_reason"], None,
                              1000, rule)  # fmt: skip
    assert ranked.tier.tolist() == ["fix first", "watch"]
    assert ranked.tier_reason[0] == (
        "Fix first: among the busiest 50% of places here (about 100 people use it), "
        "though it has 2 risk points."
    )
    assert ranked.tier_reason[1] == (
        "Watch: about 10 people use it, but it has 2 risk points; fix first needs 3, "
        "or about 100 people or more, which would put it among the busiest 50% of places "
        "and over the minimum (this has about 10)."
    )


def test_the_busiest_share_still_needs_the_people_bar() -> None:
    rule = FixFirst(busiest_share=1.0)
    assert exposure_rules.tier_of(1, 4, 1000, rule, busiest=1) == "watch"  # under 5 people
    assert exposure_rules.tier_of(1, 6, 1000, rule, busiest=1) == "fix first"
    assert exposure_rules.tier_of(1, 6, 1000, FixFirst(), busiest=math.inf) == "watch"


def test_a_raised_bar_is_used_and_stated() -> None:
    rule = FixFirst(min_points=5, min_people=20)
    assert exposure_rules.tier_of(4, 100, 1000, rule) == "watch"
    assert "fix first needs 5." in exposure_rules.tier_reason(4, 100, 1000, rule)
    assert "fewer than 20 people" in exposure_rules.tier_reason(6, 12, 1000, rule)
    streets, _ = exposure.exposure(
        _edges(), _nodes(), pd.Series({"mw": 10.0}), _shares(("mw", 10.0)), "bike",
        rules=ExposureRules(fix_first=rule),
    )  # fmt: skip
    assert streets.tier.tolist() == ["watch"]


def test_the_rule_and_points_read_plainly() -> None:
    assert fix_first_rule(FixFirst()) == (
        "Fix first: 3 or more risk points, and at least 5 people (or 5% of those counted, "
        "whichever is smaller)."
    )
    assert "or among the busiest 10% of the list's places" in fix_first_rule(
        FixFirst(busiest_share=0.1)
    )
    rows = dict(points_rows(RiskPoints()))
    assert rows["Cycling along a street, by traffic stress level 1 / 2 / 3 / 4"] == "0 / 1 / 3 / 5"
    assert rows["A signal at the crossing"] == "total × 0.25"


def test_the_busiest_share_counts_whole_places_despite_rounding() -> None:
    people = pd.Series([float(n) for n in range(100, 0, -1)])
    assert exposure_rules.busiest_edge(people, 0.07) == 94.0  # seven places, not eight


def test_a_busy_place_under_the_people_bar_is_told_so() -> None:
    rule = FixFirst(busiest_share=0.5)
    reason = exposure_rules.tier_reason(2, 4, 1000, rule, busiest=1)
    assert exposure_rules.tier_of(2, 4, 1000, rule, busiest=1) == "watch"
    assert "or about 5 people or more" in reason and "over the minimum" in reason


def test_decimal_settings_add_up_and_print_as_written() -> None:
    points = RiskPoints(crossing_band={0: 0, 1: 0.1, 2: 2, 3: 3, 4: 4}, fast=0.7)
    score, text = exposure_crossings._crossing_points(
        1, 35, 2, mode="bike", unmarked=False, signal=False, points=points
    )
    assert score == 0.8 and "(+0.1)" in text and "(+0.7)" in text
    rule = FixFirst(min_points=0.8, min_people=10.00001)
    assert exposure_rules.tier_of(score, 20, 1000, rule) == "fix first"
    assert "at least 10.00001 people" in fix_first_rule(rule)
    assert "busiest 7% " in fix_first_rule(FixFirst(busiest_share=0.07))


def test_a_share_of_those_counted_is_the_bar_as_written() -> None:
    rule = FixFirst(min_people=10, min_share=0.07)
    assert exposure_rules.tier_of(3, 7, 100, rule) == "fix first"
    tiny = RiskPoints(bike_lts={1: 0, 2: 1, 3: 1e-10, 4: 5})
    assert exposure.street_risk(_edges(), "bike", points=tiny).risk_points["mw"] == 1e-10


def test_settings_keep_the_precision_totals_are_compared_at() -> None:
    rules = rules_from(
        {"points": {"bike_lts": {3: 3.000000000001}}, "fix_first": {"min_points": 3.000000000001}}
    )
    risk = exposure.street_risk(_edges(), "bike", points=rules.points)
    assert (
        exposure_rules.tier_of(float(risk.risk_points["mw"]), 10, 100, rules.fix_first)
        == "fix first"
    )


def test_people_are_compared_at_the_bars_precision() -> None:
    table = pd.DataFrame(
        {"place_id": ["a"], "people": [0.1 + 0.7], "risk_points": [3.0], "exposure": [2.4],
         "geometry": [None]}
    )  # fmt: skip
    ranked = exposure_lists.ranked(table, [*table.columns, "rank", "tier", "tier_reason"], None,
                              16, FixFirst())  # fmt: skip
    assert ranked.tier.tolist() == ["fix first"]  # 5% of 16 counted is 0.8


def test_the_busiest_cutoff_is_taken_at_the_same_precision() -> None:
    table = pd.DataFrame(
        {"place_id": ["a"], "people": [0.1 + 0.2], "risk_points": [2.0], "exposure": [0.6],
         "geometry": [None]}
    )  # fmt: skip
    ranked = exposure_lists.ranked(table, [*table.columns, "rank", "tier", "tier_reason"], None,
                              1, FixFirst(busiest_share=1))  # fmt: skip
    assert ranked.tier.tolist() == ["fix first"]


def test_the_busiest_share_of_car_streets_is_that_share() -> None:
    cars = pd.Series({"mw": 40.0, "me": 30.0, "on": 20.0, "os": 10.0})
    lts3: dict[str, dict[str, object]] = {
        pid: {"lts_best": 3, "name": pid} for pid in ("mw", "me", "on", "os")
    }
    risk = exposure.street_risk(_edges(**lts3), "bike", cars, RiskPoints(drop_off_share=0.5))
    busy = risk.risk_factors.str.contains("drop-off")
    assert sorted(busy[busy].index) == ["me", "mw"]  # half of four streets


def test_streets_tied_at_the_edge_of_the_busiest_share_all_count() -> None:
    cars = pd.Series({"mw": 10.0, "me": 10.0, "on": 3.0, "os": 2.0})
    lts3: dict[str, dict[str, object]] = {
        pid: {"lts_best": 3, "name": pid} for pid in ("mw", "me", "on", "os")
    }
    risk = exposure.street_risk(_edges(**lts3), "bike", cars, RiskPoints(drop_off_share=0.1))
    busy = risk.risk_factors.str.contains("drop-off")
    assert sorted(busy[busy].index) == ["me", "mw"]


def test_a_configured_bar_reads_as_set() -> None:
    assert "fix first needs 3.001." in exposure_rules.tier_reason(
        2, 10, 1000, FixFirst(min_points=3.001)
    )
    rule = FixFirst(min_people=5.0001)
    assert "fewer than 5.0001 people" in exposure_rules.tier_reason(4, 5, 1000, rule)


def test_the_map_keeps_fine_points() -> None:
    from walkbike_routes.report import map as report

    assert report._significant(1e-10) == 1e-10 and report._significant(0.75) == 0.75
    assert report._significant(float("nan")) is None and report._significant(None) is None


def test_a_null_setting_is_refused_except_to_turn_the_busiest_rule_off() -> None:
    with pytest.raises(ValueError, match="exposure.points.fast: expected a value, not null"):
        rules_from({"points": {"fast": None}})
    with pytest.raises(ValueError, match="exposure.fix_first.min_points: expected a value"):
        rules_from({"fix_first": {"min_points": None}})
    assert rules_from({"fix_first": {"busiest_share": None}}).fix_first.busiest_share is None


def test_the_busiest_reason_states_a_configured_people_bar_as_set() -> None:
    rule = FixFirst(min_people=5.0001, busiest_share=1)
    assert "or about 5.0001 people or more" in exposure_rules.tier_reason(
        2, 4, 1000, rule, busiest=4
    )


def test_a_reason_counts_in_the_runs_own_words_and_whole_numbers() -> None:
    reason = exposure_rules.tier_reason
    assert reason(4, 114.2, 1000, flow="expected trips") == (
        "Fix first: 4 risk points (fix first needs 3), and about 114 expected trips use it."
    )
    # Decimals only where a whole number would misstate the comparison with the bar.
    assert reason(4, 4.6, 1000, flow="students").endswith(
        "fewer than 5 students use it (about 4.6)."
    )
    assert "(about 0.3)" in reason(4, 0.3, 1000)
    # Exactly one takes the singular.
    assert reason(4, 1, 20, flow="expected trips").endswith("and about 1 expected trip uses it.")
    assert reason(4, 1, 20).endswith("and about 1 person uses it.")
    assert "about 1 resident aged 5-11 uses it" in reason(4, 1.2, 20, flow="residents aged 5-11")
    assert "fewer than 1 household uses it (about 0.4" in reason(
        4, 0.4, 20, FixFirst(min_people=1, min_share=1), flow="households"
    )
    busy = reason(2, 7.4, 1000, FixFirst(busiest_share=0.1), busiest=12.3, flow="expected trips")
    assert "or about 12 expected trips or more" in busy and "(this has about 7)" in busy


def test_lists_for_several_destinations_use_a_shared_word_or_people() -> None:
    def both(first: str, second: str) -> str:
        runs = {
            "a": exposure.RunFlows(pd.Series({"mw": 10.0}), _shares(), flow_noun=first),
            "b": exposure.RunFlows(pd.Series({"mw": 10.0}), _shares(), flow_noun=second),
        }
        streets, _ = exposure.combined_exposure(_edges(), _nodes(), runs, "bike")
        return str(streets.tier_reason.iloc[0])

    assert "expected trips use it" in both("expected trips", "expected trips")
    assert "people use it" in both("students aged 11-13", "students aged 14-17")
