"""Distance-decay curves, the config that picks them, and their use in a run."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import yaml

from walkbike_routes import config, demo, study
from walkbike_routes.demand import decay

DEMO = Path(__file__).resolve().parents[1] / "config" / "demo.yaml"
MILE = decay.METRES_PER_MILE


def _curve(**changes: Any) -> decay.DecayCurve:
    values: dict[str, Any] = {
        "name": "test", "mode": "walk", "title": "t", "source": "made up", "trips": 10,
        "median_miles": 0.5, "miles": (0.0, 1.0, 2.0), "share": (1.0, 0.5, 0.0),
    }  # fmt: skip
    values.update(changes)
    return decay.DecayCurve(**values)


def test_a_curve_interpolates_and_holds_its_last_share() -> None:
    curve = _curve(share=(1.0, 0.5, 0.1))
    got = curve.factor([0.0, 0.5 * MILE, 1.5 * MILE, 9 * MILE, np.nan])
    assert got == pytest.approx([1.0, 0.75, 0.3, 0.1, 0.0])


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"miles": (0.0, 1.0), "share": (1.0,)}, "matching"),
        ({"miles": (0.5, 1.0, 2.0)}, "start at 0"),
        ({"share": (0.9, 0.5, 0.0)}, "start at 0"),
        ({"miles": (0.0, 2.0, 1.0)}, "increase"),
        ({"share": (1.0, 0.2, 0.4)}, "not increase"),
        ({"share": (1.0, 0.5, -0.1)}, "negative"),
        ({"share": (1.0, float("nan"), 0.0)}, "finite"),
        ({"miles": (0.0, 1.0, float("inf"))}, "finite"),
    ],
)
def test_malformed_curves_are_refused(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(decay.DecayError, match=message):
        _curve(**changes)


def test_every_shipped_curve_loads_and_falls_from_one() -> None:
    found = decay.names()
    assert {"walk-everyday", "bike-everyday", "walk-school-5-10", "bike-school-5-17"} <= set(found)
    for name in found:
        curve = decay.load(name)
        assert curve.name == name and curve.share[0] == 1.0 and curve.share[-1] < 0.05
        assert curve.trips > 200 and "2017 National Household Travel Survey" in curve.source
    assert set(decay.names("bike")) == {n for n in found if n.startswith("bike-")}


@pytest.mark.parametrize("name", ["nope", "../walk/legacy", ""])
def test_unknown_curves_are_refused(name: str) -> None:
    with pytest.raises(decay.DecayError, match="no decay curve"):
        decay.load(name)


def test_survival_and_median_are_weighted() -> None:
    miles = [0.1, 0.6, 1.2, np.nan, 3.0]
    weights = [3.0, 1.0, 1.0, 5.0, 0.0]  # the unknown length and the zero weight drop out
    assert decay.survival(miles, weights, [0, 0.5, 1, 2]) == pytest.approx([1, 0.4, 0.2, 0])
    assert decay.weighted_median(miles, weights) == pytest.approx(0.1)
    with pytest.raises(decay.DecayError):
        decay.survival([np.nan], [1.0], [0])
    with pytest.raises(decay.DecayError):
        decay.weighted_median([1.0], [0.0])


def test_apply_weights_homes_within_the_limit_only() -> None:
    routes = pd.DataFrame(
        {
            "demand_weight": [10.0, 10.0, 10.0, 10.0],
            "physical_length_m": [0.0, 0.9 * MILE, 1.5 * MILE, np.nan],
            "snap_distance_m": [0.0, 0.1 * MILE, np.nan, np.nan],
            "route_reachable": [True, True, True, False],
            "within_time_limit": [True, True, False, False],
        }
    )
    got = decay.apply(routes, _curve())
    assert got.decay_factor.tolist() == pytest.approx([1.0, 0.5, 0.25, 0.0])
    assert got.expected_weight.tolist() == pytest.approx([10.0, 5.0, 0.0, 0.0])
    hard = decay.apply(routes, None)
    assert hard.expected_weight.tolist() == pytest.approx([10.0, 10.0, 0.0, 0.0])


def _raw(**changes: Any) -> dict[str, Any]:
    raw = copy.deepcopy(yaml.safe_load(DEMO.read_text()))
    raw.update(changes)
    return raw


def _destination(**changes: Any) -> config.Destination:
    values: dict[str, Any] = {"id": "d", "name": "D", "kind": "point"}
    values.update(changes)
    return config.Destination(**values)


@pytest.mark.parametrize(
    ("destination", "mode", "expected"),
    [
        ({}, "walk", "walk-everyday"),
        ({"purpose": "work"}, "bike", "bike-work"),
        ({"purpose": "school"}, "bike", "bike-school-5-17"),
        ({"purpose": "school", "min_age": 4, "max_age": 11}, "walk", "walk-school-5-10"),
        ({"purpose": "school", "min_age": 12, "max_age": 13}, "walk", "walk-school-11-13"),
        ({"purpose": "school", "min_age": 14, "max_age": 17}, "walk", "walk-school-14-17"),
        ({"purpose": "school", "min_age": 15}, "walk", "walk-school-14-17"),
        ({"purpose": "school"}, "car", None),
    ],
)
def test_the_destination_picks_its_curve(
    destination: dict[str, Any], mode: str, expected: str | None
) -> None:
    settings = config.parse(_raw(demand={"weight": "residents"}), base=DEMO.parent)
    assert config.decay_for(settings, _destination(**destination), mode) == expected


def test_a_study_without_ages_uses_the_all_ages_school_curve() -> None:
    raw = _raw(demand={"weight": "households"})
    for destination in raw["destinations"]:  # households have no ages
        destination.pop("min_age", None)
        destination.pop("max_age", None)
    settings = config.parse(raw, base=DEMO.parent)
    assert config.decay_for(settings, _destination(purpose="school"), "walk") == "walk-school-5-17"


def test_the_study_can_name_curves_or_turn_decay_off() -> None:
    named = config.parse(_raw(distance_decay={"walk": "walk-work"}), base=DEMO.parent)
    assert config.decay_for(named, _destination(purpose="school"), "walk") == "walk-work"
    assert config.decay_for(named, _destination(), "bike") == "bike-everyday"
    off = config.parse(_raw(distance_decay="none"), base=DEMO.parent)
    assert off.distance_decay == {"walk": "none", "bike": "none"}
    assert config.decay_for(off, _destination(purpose="school"), "walk") is None
    walk_off = config.parse(_raw(distance_decay={"walk": "none"}), base=DEMO.parent)
    assert config.decay_for(walk_off, _destination(), "walk") is None
    assert config.decay_for(walk_off, _destination(), "bike") == "bike-everyday"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"distance_decay": {"car": "walk-work"}}, "walk or bike"),
        ({"distance_decay": {"bike": "bike-work"}, "modes": ["walk"]}, "walk or bike"),
        ({"distance_decay": {"walk": "bike-work"}}, "is a bike curve"),
        ({"distance_decay": {"walk": "nope"}}, "no decay curve"),
        ({"distance_decay": "hard"}, "expected a mapping"),
        (
            {"destinations": [{"id": "x", "name": "X", "kind": "point", "purpose": "fun"}]},
            "purpose",
        ),
    ],
)
def test_bad_decay_settings_are_refused(changes: dict[str, Any], message: str) -> None:
    raw = _raw(**changes)
    if "modes" in changes:
        raw["presets"] = {"walk": ["legacy"]}
        raw["time_caps_min"] = {"walk": 20}
    with pytest.raises(config.ConfigError, match=message):
        config.parse(raw, base=DEMO.parent)


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    town = demo.build()
    out = {}
    for label, decay_setting in (("decay", {}), ("hard", "none")):
        raw = _raw(modes=["walk"], distance_decay=decay_setting)
        raw["presets"] = {"walk": ["legacy"]}
        raw["time_caps_min"] = {"walk": 20}
        folder = tmp_path_factory.mktemp(label)
        study.analyze(config.parse(raw, base=DEMO.parent), town, folder)
        out[label] = folder
    return out


def test_decay_lowers_flows_but_not_catchments(runs: dict[str, Path]) -> None:
    decayed = pd.read_csv(runs["decay"] / "summary.csv").set_index("destination_id")
    hard = pd.read_csv(runs["hard"] / "summary.csv").set_index("destination_id")
    assert decayed.decay_curve.to_dict() == {"school": "walk-school-5-10",
                                             "downtown": "walk-everyday"}  # fmt: skip
    assert set(hard.decay_curve) == {"none"}
    assert decayed.within_cap_weight.equals(hard.within_cap_weight)
    assert hard.expected_weight.to_numpy() == pytest.approx(hard.within_cap_weight.to_numpy())
    assert (decayed.expected_weight < 0.9 * hard.expected_weight).all()
    flows = {
        k: gpd.read_parquet(v / "school" / "walk-legacy" / "flows.parquet")
        for k, v in runs.items()
    }  # fmt: skip
    assert flows["decay"].potential_flow.sum() < flows["hard"].potential_flow.sum()
    routes = pd.read_parquet(runs["decay"] / "school" / "walk-legacy" / "routes.parquet")
    shares = pd.read_parquet(runs["decay"] / "school" / "walk-legacy" / "route_shares.parquet")
    assert shares.assigned_weight.sum() == pytest.approx(routes.expected_weight.sum())
