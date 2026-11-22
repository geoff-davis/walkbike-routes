"""Traffic stress and walking-evidence rules, case by case."""

from __future__ import annotations

import math
from typing import Any

import pytest

from walkbike_routes.network.bicycle import (
    BicycleProfile,
    classify_lts,
    infer_traffic_band,
    parse_number,
)
from walkbike_routes.network.pedestrian import (
    PedestrianProfile,
    crossing_markings_indicate_marked,
    crossing_markings_indicate_unmarked,
    pedestrian_status,
)
from walkbike_routes.numeric import require_finite, require_finite_settings

ROAD = {"highway": "residential", "speed_mph": 25, "lanes": 2, "traffic_band": "low"}


@pytest.mark.parametrize(
    ("change", "lts"),
    [
        ({}, 1),
        ({"bike_allowed": False}, 4),
        ({"bike_facility": "track", "highway": "primary", "speed_mph": 45}, 1),
        ({"bike_facility": "Class IV (protected)", "highway": "primary"}, 1),
        ({"highway": "cycleway"}, 1),
        ({"highway": "cycleway", "motor_vehicle": "yes", "speed_mph": 40}, 4),
        ({"bike_facility": "lane", "speed_mph": 30, "traffic_band": "moderate"}, 2),
        ({"bike_facility": "Class II", "speed_mph": 35, "traffic_band": "high"}, 3),
        ({"bike_facility": "bike lane", "speed_mph": 45}, 4),
        ({"speed_mph": 30, "traffic_band": "moderate"}, 2),
        ({"speed_mph": 35, "lanes": 3, "traffic_band": "high"}, 3),
        ({"speed_mph": 40}, 4),
        ({"lanes": 4}, 4),
    ],
)
def test_stress_levels_follow_the_published_thresholds(change: dict[str, Any], lts: int) -> None:
    assert classify_lts({**ROAD, **change}).lts == lts


def test_missing_attributes_take_the_road_class_defaults_and_are_reported() -> None:
    bare = classify_lts({"highway": "tertiary"})
    assert bare.lts == 2  # 30 mph, moderate traffic, 2 lanes
    assert bare.missing == "posted_speed,traffic_volume_band,lane_count"
    assert bare.inference_dependent
    nan_band = classify_lts({**ROAD, "traffic_band": math.nan})
    assert "traffic_volume_band" in nan_band.missing


def test_an_inferred_band_is_reported_as_inferred() -> None:
    edge = {**ROAD, "traffic_band": "moderate", "traffic_volume_type": "inferred traffic band"}
    assert classify_lts(edge).lts == 2
    assert "observed_traffic_volume" in classify_lts(edge).missing


@pytest.mark.parametrize(
    ("args", "band"),
    [
        (("footway", None, None), "no_motor_traffic"),
        (("motorway", None, None), "very_high"),
        (("residential", 4, None), "high"),
        (("residential", None, 40), "high"),
        (("residential", 3, None), "moderate"),
        (("tertiary", None, None), "moderate"),
        (("residential", 2, 25), "low"),
        (("service", None, None), "very_low"),
        ((None, None, None), "unknown"),
    ],
)
def test_traffic_bands_are_inferred_from_road_class_lanes_and_speed(
    args: tuple[Any, ...], band: str
) -> None:
    assert infer_traffic_band(*args)[0] == band


@pytest.mark.parametrize(
    ("raw", "number"),
    [
        ("12,500", 12500.0),
        ("35 mph", 35.0),
        ("0", None),
        ("", None),
        (None, None),
        ("n/a", None),
        ("1.2.3", None),
    ],
)
def test_numbers_are_read_from_agency_text(raw: Any, number: float | None) -> None:
    assert parse_number(raw) == number


@pytest.mark.parametrize(
    ("edge", "status"),
    [
        ({"ped_allowed": False}, "problematic"),
        ({"movement_type": "across", "crossing_control_state": "signal_osm"}, "supported"),
        (
            {
                "movement_type": "across",
                "crossing_control_state": "signal_local",
                "curb_ramp_state": "installed_local",
            },
            "supported",
        ),
        ({"movement_type": "across", "crossing": "zebra"}, "supported"),
        ({"movement_type": "across", "crossing": "unmarked"}, "problematic"),
        ({"movement_type": "across", "curb_ramp_state": "known_absent_local"}, "problematic"),
        ({"movement_type": "across", "curb_ramp_state": "installed_local"}, "unknown"),
        ({"movement_type": "across"}, "unknown"),
        ({"highway": "footway"}, "supported"),
        ({"highway": "residential", "sidewalk_state": "present_osm"}, "supported"),
        ({"highway": "residential", "sidewalk_state": "absent_manual"}, "problematic"),
        ({"highway": "primary", "sidewalk_state": "absent_osm_explicit"}, "problematic"),
        ({"highway": "residential", "sidewalk_state": "absent_osm_explicit"}, "unknown"),
        ({"highway": "residential"}, "unknown"),
    ],
)
def test_walking_evidence_is_graded(edge: dict[str, Any], status: str) -> None:
    assert pedestrian_status(edge)[0] == status


@pytest.mark.parametrize(
    ("value", "marked", "unmarked"),
    [
        ("zebra", True, False),
        ("lines;zebra:double", True, False),
        ("no", False, True),
        (None, False, False),
        ("odd", False, False),
    ],
)
def test_crossing_markings(value: Any, marked: bool, unmarked: bool) -> None:
    assert crossing_markings_indicate_marked(value) is marked
    assert crossing_markings_indicate_unmarked(value) is unmarked


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda: BicycleProfile("x", speed_mph=0), "speed must be positive"),
        (lambda: BicycleProfile("x", lts_cost_factors=(1, 1, 0, 1)), "factors must be positive"),
        (lambda: BicycleProfile("x", signal_wait_s=-1), "nonnegative"),
        (lambda: BicycleProfile("x", speed_mph=math.inf), "finite"),
        (lambda: BicycleProfile("x").factor(5), "invalid LTS"),
        (lambda: PedestrianProfile("x", unknown_local_factor=0), "positive"),
        (lambda: PedestrianProfile("x", speed_mph=-1), "speed must be positive"),
        (lambda: PedestrianProfile("x", signal_wait_s=-1), "nonnegative"),
    ],
)
def test_bad_profiles_are_refused(make: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        make()


def test_finite_checks_reach_inside_settings() -> None:
    require_finite(0.0, "x")
    with pytest.raises(ValueError, match="greater than 1"):
        require_finite(1.0, "x", minimum=1, positive=True)
    with pytest.raises(ValueError, match="at least 0"):
        require_finite(-1.0, "x")
    with pytest.raises(ValueError, match=r"config\.a\[1\] must be finite"):
        require_finite_settings({"a": [1.0, math.nan]})
