"""Route-choice presets."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString

from walkbike_routes import config
from walkbike_routes.costs import presets
from walkbike_routes.network.bicycle import BicycleProfile
from walkbike_routes.network.motor import MotorPenaltyProfile
from walkbike_routes.network.pedestrian import (
    PedestrianProfile,
    build_pedestrian_links,
    evidence_factor,
    pedestrian_status,
)


@pytest.mark.parametrize("mode", presets.MODES)
def test_every_shipped_preset_loads_and_names_its_source(mode: str) -> None:
    assert presets.names(mode)
    for name in presets.names(mode):
        preset = presets.load(mode, name)
        assert preset.mode == mode and preset.name == name and len(preset.source) > 40


def test_the_legacy_presets_match_the_los_altos_settings() -> None:
    bike = presets.load("bike", "legacy").profile()
    assert isinstance(bike, BicycleProfile)
    assert bike.lts_cost_factors == (1.0, 1.25, 3.0, 8.0) and bike.speed_mph == 10
    assert (bike.unknown_crossing_cost_s, bike.uncontrolled_crossing_cost_s) == (15, 30)
    assert bike.signal_wait_s == 20
    assert presets.load("bike", "shortest").objective == "physical_length_m"
    walk = presets.load("walk", "legacy").profile()
    assert isinstance(walk, PedestrianProfile)
    assert walk.allow_unknown_local and not walk.allow_unknown_major and walk.signal_wait_s == 30
    car = presets.load("car", "central").profile()
    assert isinstance(car, MotorPenaltyProfile)
    assert (car.signal_s, car.stop_s, car.left_turn_s, car.u_turn_s) == (20, 5, 7.5, 27.5)


def test_the_child_presets_are_slower_and_more_cautious_but_ban_nothing() -> None:
    adult_bike, child_bike = (presets.load("bike", n).profile() for n in ("legacy-hills", "child"))
    assert isinstance(adult_bike, BicycleProfile) and isinstance(child_bike, BicycleProfile)
    assert child_bike.speed_mph == 6 and child_bike.grade_speeds and child_bike.maximum_lts is None
    assert child_bike.lts_cost_factors == (1.0, 1.5, 6.0, 20.0)
    assert all(c >= a for c, a in zip(child_bike.lts_cost_factors, adult_bike.lts_cost_factors,
                                      strict=True))  # fmt: skip
    walk = presets.load("walk", "child").profile()
    assert isinstance(walk, PedestrianProfile)
    assert walk.speed_mph == 2.5 and walk.grade_speeds
    for highway, factor in (("residential", 1.5), ("tertiary", 4.0), ("primary", 12.0),
                            ("footway", 4.0)):  # fmt: skip
        assert evidence_factor(walk, "unknown", highway) == factor
        assert evidence_factor(walk, "problematic", highway) == factor
    legacy = presets.load("walk", "legacy").profile()
    assert isinstance(legacy, PedestrianProfile)
    assert (
        evidence_factor(legacy, "problematic", "residential") is None
    )  # adults still leave them out
    assert evidence_factor(legacy, "unknown", "primary") is None


def test_no_preset_walks_across_where_crossing_is_not_allowed() -> None:
    edges = gpd.GeoDataFrame(
        {"physical_edge_id": ["x", "y", "z", "w"], "u": ["a", "b", "c", "d"],
         "v": ["b", "c", "d", "e"], "highway": ["footway"] * 4,
         "movement_type": ["across", "across", "along", "along"],
         "crossing": pd.array(["NO", "unmarked", "no", pd.NA], dtype="string"),
         "length_m": [10.0] * 4},
        geometry=[LineString([(10 * i, 0), (10 * i + 10, 0)]) for i in range(4)], crs=26910,
    )  # fmt: skip
    records = [{str(k): v for k, v in e.items()} for e in edges.to_dict("records")]
    edges["pedestrian_status"] = [pedestrian_status(e)[0] for e in records]
    assert edges.pedestrian_status.tolist()[:2] == ["problematic", "problematic"]
    # A path along the street tagged crossing=no is kept: walking along it is allowed.
    for name, kept in (("legacy", {"z", "w"}), ("child", {"y", "z", "w"})):
        profile = presets.load("walk", name).profile()
        assert isinstance(profile, PedestrianProfile)
        assert set(build_pedestrian_links(edges, profile).physical_edge_id) == kept


def test_the_new_switch_does_not_move_positional_settings() -> None:
    profile = PedestrianProfile("p", 3, True, False, False, 1.25, 2, 2, 5, 10)
    assert profile.signal_wait_s == 10 and not profile.allow_problematic


def test_an_unknown_preset_is_refused_with_the_choices() -> None:
    with pytest.raises(
        presets.PresetError,
        match="choose from: broach-2016, broach-2016-child, broach-2016-sidewalks, child, legacy",
    ):
        presets.load("walk", "brisk")
    with pytest.raises(presets.PresetError, match="unknown mode"):
        presets.names("skate")


def test_bad_parameters_are_caught_when_the_preset_is_read() -> None:
    preset = presets.load("bike", "legacy")
    broken = presets.Preset(**{**preset.__dict__, "parameters": {"speed_kph": 16}})
    with pytest.raises(presets.PresetError, match="speed_kph"):
        broken.profile()


def test_a_study_naming_a_missing_preset_is_refused() -> None:
    raw = {
        "study": "X",
        "area": {"source": "demo"},
        "destinations": [{"id": "school", "name": "S", "kind": "point"}],
        "modes": ["bike"],
        "presets": {"bike": ["legacy", "fast"]},
        "time_caps_min": {"bike": 15},
        "demand": {"weight": "households"},
    }
    with pytest.raises(config.ConfigError, match="no bike preset 'fast'"):
        config.parse(raw)
