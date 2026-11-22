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


def test_the_presets_carry_their_settings() -> None:
    bike = presets.load("bike", "furth").profile()
    assert isinstance(bike, BicycleProfile)
    assert bike.lts_cost_factors == (1.0, 1.25, 3.0, 8.0) and bike.speed_mph == 10
    assert (bike.unknown_crossing_cost_s, bike.uncontrolled_crossing_cost_s) == (15, 30)
    assert bike.signal_wait_s == 20 and bike.grade_speeds and bike.lts_criteria == "furth"
    assert presets.load("bike", "shortest").objective == "physical_length_m"
    walk = presets.load("walk", "shortest").profile()
    assert isinstance(walk, PedestrianProfile)
    assert walk.signal_wait_s == 30
    assert evidence_factor(walk, "unknown", "primary") == 1.0
    assert evidence_factor(walk, "problematic", "primary") == 1.0
    assert presets.load("walk", "shortest").objective == "physical_length_m"
    car = presets.load("car", "central").profile()
    assert isinstance(car, MotorPenaltyProfile)
    assert (car.signal_s, car.stop_s, car.left_turn_s, car.u_turn_s) == (20, 5, 7.5, 27.5)


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
    # A path along the street tagged crossing=no is kept: walking along it is
    # allowed.  The unmarked crossing is kept too, at a cost: poor evidence is
    # a penalty, never a ban.
    shortest = presets.load("walk", "shortest").profile()
    assert isinstance(shortest, PedestrianProfile)
    assert set(build_pedestrian_links(edges, shortest).physical_edge_id) == {"y", "z", "w"}
    links = build_pedestrian_links(edges, PedestrianProfile("default"))
    assert set(links.physical_edge_id) == {"y", "z", "w"}
    unmarked = links[links.physical_edge_id.eq("y")]
    assert unmarked.facility_cost_s.gt(0).all()


def test_an_unknown_preset_is_refused_with_the_choices() -> None:
    with pytest.raises(
        presets.PresetError,
        match="choose from: broach-2016, broach-2016-child, broach-2016-sidewalks, shortest",
    ):
        presets.load("walk", "brisk")
    with pytest.raises(presets.PresetError, match="unknown mode"):
        presets.names("skate")


def test_bad_parameters_are_caught_when_the_preset_is_read() -> None:
    preset = presets.load("bike", "furth")
    broken = presets.Preset(**{**preset.__dict__, "parameters": {"speed_kph": 16}})
    with pytest.raises(presets.PresetError, match="speed_kph"):
        broken.profile()


def test_a_study_naming_a_missing_preset_is_refused() -> None:
    raw = {
        "study": "X",
        "area": {"source": "demo"},
        "destinations": [{"id": "school", "name": "S", "kind": "point"}],
        "modes": ["bike"],
        "presets": {"bike": ["shortest", "fast"]},
        "time_caps_min": {"bike": 15},
        "demand": {"weight": "households"},
    }
    with pytest.raises(config.ConfigError, match="no bike preset 'fast'"):
        config.parse(raw)
