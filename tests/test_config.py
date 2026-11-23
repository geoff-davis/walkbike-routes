"""Study configs: what is accepted and what is refused."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from walkbike_routes import config

ROOT = Path(__file__).resolve().parents[1]

GOOD: dict[str, Any] = {
    "study": "Somewhere",
    "area": {"source": "place", "place": "Somewhere, Oregon", "buffer_m": 500},
    "destinations": [
        {
            "id": "school",
            "name": "A School",
            "kind": "point",
            "location": [-122.1, 37.4],
            "access_points": [[-122.1001, 37.4001]],
        },
        {"id": "centre", "name": "Town centre", "kind": "area", "boundary_file": "centre.geojson"},
    ],
    "modes": ["walk", "bike"],
    "presets": {"walk": ["shortest"], "bike": "shortest"},
    "time_caps_min": {"walk": 20, "bike": 15},
    "demand": {"weight": "residents", "min_age": 5, "max_age": 11},
}


def changed(path: str, value: Any) -> dict[str, Any]:
    """GOOD with one setting replaced; a value of ``...`` removes it."""
    raw = copy.deepcopy(GOOD)
    *parents, last = path.split(".")
    here: Any = raw
    for key in parents:
        here = here[int(key)] if isinstance(here, list) else here[key]
    if value is ...:
        del here[last]
    else:
        here[last] = value
    return raw


def test_the_demo_config_loads() -> None:
    study = config.load(ROOT / "config" / "demo.yaml")
    assert study.synthetic and study.area.source == "demo"
    assert [d.id for d in study.destinations] == ["school", "downtown"]
    assert study.presets["car"] == ("central",) and "car" not in study.time_caps_min


def test_a_good_config_is_read_into_its_parts(tmp_path: Path) -> None:
    study = config.parse(GOOD, base=tmp_path)
    assert study.modes == ("walk", "bike")
    assert study.presets == {"walk": ("shortest",), "bike": ("shortest",)}
    assert study.time_caps_min == {"walk": 20.0, "bike": 15.0}
    school, centre = study.destinations
    assert school.location == (-122.1, 37.4) and school.access_points == ((-122.1001, 37.4001),)
    assert centre.boundary_file == (tmp_path / "centre.geojson").resolve()
    assert study.crs is None and not study.synthetic
    assert config.parse(changed("crs", "EPSG:32610")).crs == "EPSG:32610"


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        ("colour", "red", "unknown setting 'colour'"),
        ("demand", ..., "missing setting 'demand'"),
        ("area.source", "moon", "not one of demo, place, boundary_file"),
        ("area.place", ..., "needs a place name"),
        ("area.buffer_m", -1, "must not be negative"),
        ("modes", ["walk", "skate"], "not one of walk, bike, car"),
        ("modes", ["walk", "walk"], "each mode once"),
        ("modes", [], "list at least one"),
        ("presets.car", ["central"], "'car' is not one of the study's modes"),
        ("presets.bike", ..., "missing a value for bike"),
        ("presets.bike", [], "name at least one preset"),
        ("time_caps_min.bike", ..., "missing a value for bike"),
        ("time_caps_min.walk", 0, "positive number"),
        ("time_caps_min.walk", True, "positive number"),
        ("destinations", [], "name at least one"),
        ("destinations.1.id", "school", "ids must be unique"),
        ("destinations.0.kind", "line", "not one of point, area"),
        ("destinations.0.location", ..., "needs a location"),
        ("destinations.0.location", [37.4, -200], "not a longitude and latitude"),
        ("destinations.0.location", [1, 2, 3], "expected [longitude, latitude]"),
        ("destinations.1.boundary_file", ..., "needs a boundary_file"),
        ("demand.weight", "cars", "not one of residents, households"),
        ("demand.min_age", 12, "min_age is above max_age"),
        ("demand.min_age", 11.9, "whole number of years"),
        ("demand.min_age", True, "whole number of years"),
        ("demand.max_age", "abc", "whole number of years"),
        ("synthetic", "false", "expected true or false"),
        ("time_caps_min.walk", float("nan"), "positive number"),
        ("time_caps_min.walk", float("inf"), "positive number"),
        ("area.buffer_m", float("nan"), "expected a number"),
        ("area.buffer_m", "abc", "expected a number"),
        ("area.buffer_m", None, "expected a number"),
        ("destinations.0.access_points", False, "expected a list"),
        ("destinations.0.access_points", 0, "expected a list"),
        ("crs", "EPSG:4326", "not a projected system in metres"),
        ("crs", "EPSG:2227", "not a projected system in metres"),
        ("crs", "nonsense", "not a known coordinate system"),
    ],
)
def test_a_bad_setting_is_refused_with_the_reason(path: str, value: Any, message: str) -> None:
    with pytest.raises(config.ConfigError, match=message.replace("[", r"\[")):
        config.parse(changed(path, value))


def test_households_take_no_ages() -> None:
    raw = changed("demand", {"weight": "households", "max_age": 11})
    with pytest.raises(config.ConfigError, match="ages apply to residents"):
        config.parse(raw)


def test_the_demo_area_needs_no_locations() -> None:
    raw = changed("area", {"source": "demo"})
    raw["destinations"] = [{"id": "school", "name": "S", "kind": "point"}]
    assert config.parse(raw).destinations[0].location is None


def test_a_missing_or_broken_config_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match="no such config file"):
        config.load(tmp_path / "missing.yaml")
    broken = tmp_path / "broken.yaml"
    broken.write_text("study: [unclosed\n")
    with pytest.raises(config.ConfigError, match="not valid YAML"):
        config.load(broken)


@pytest.mark.parametrize(
    "path",
    sorted(p for p in (ROOT / "config").glob("*.yaml") if config.local_file(p)),
    ids=lambda p: p.name,
)
def test_every_example_config_reads(path: Path) -> None:
    study = config.load(path)
    assert (study.destinations or study.network_flows) and study.modes
    if study.area.boundary_file is not None:
        assert study.area.boundary_file.exists()


def test_a_destination_can_have_its_own_ages_and_area() -> None:
    raw = copy.deepcopy(GOOD)
    raw["destinations"][0].update(min_age=14, max_age=17, serves="area.geojson")
    study = config.parse(raw, base=Path("/tmp/x"))
    school = study.destinations[0]
    assert (school.min_age, school.max_age, school.serves) == (14, 17, Path("/tmp/x/area.geojson"))
    assert config.demand_for(study, school) == config.Demand("residents", 14, 17)
    raw["destinations"][0].update(min_age=18)
    with pytest.raises(config.ConfigError, match="min_age is above max_age"):
        config.parse(raw)
    raw["destinations"][0].update(min_age=14)
    raw["demand"] = {"weight": "households"}
    with pytest.raises(config.ConfigError, match="ages apply to residents"):
        config.parse(raw)


def _write_with_private(tmp_path: Path, private: dict[Any, Any]) -> Path:
    raw = copy.deepcopy(GOOD)
    raw["local_layers"] = [{"kind": "signals", "file": "public_poles.gpkg"}]
    path = tmp_path / "town.yaml"
    path.write_text(yaml.safe_dump(raw))
    (tmp_path / "town.local.yaml").write_text(yaml.safe_dump(private))
    return path


def test_a_private_local_file_adds_its_layers_after_the_configs_own(tmp_path: Path) -> None:
    path = _write_with_private(
        tmp_path, {"local_layers": [{"kind": "sidewalks", "file": "city/sidewalks.gpkg"}]}
    )
    study = config.load(path)
    assert [layer.kind for layer in study.local_layers] == ["signals", "sidewalks"]
    assert study.local_layers[1].file == (tmp_path / "city" / "sidewalks.gpkg").resolve()
    assert study.local_source == tmp_path / "town.local.yaml"
    public = config.load(path, local=False)
    assert [layer.kind for layer in public.local_layers] == ["signals"]
    assert public.local_source is None


def test_a_private_local_file_may_hold_only_local_layers(tmp_path: Path) -> None:
    path = _write_with_private(tmp_path, {"local_layers": [], "presets": {"walk": ["shortest"]}})
    with pytest.raises(
        config.ConfigError, match="only local_layers and outlines may go here, not 'presets'"
    ):
        config.load(path)
    path = _write_with_private(tmp_path, {1: None, "unexpected": None})
    with pytest.raises(
        config.ConfigError, match="only local_layers and outlines may go here, not 1"
    ):
        config.load(path)


def test_an_outline_comes_from_the_config_or_the_private_file(tmp_path: Path) -> None:
    raw = copy.deepcopy(GOOD)
    raw["destinations"][0]["outline"] = {
        "file": "areas.geojson",
        "where": {"SCHOOL": ["A", "B "]},
        "credit": "A school district",
    }
    drawn = config.parse(raw, base=Path("/tmp/x")).destinations[0].outline
    assert drawn == config.Outline(
        file=Path("/tmp/x/areas.geojson"),
        where={"SCHOOL": ("A", "B")},
        credit="A school district",
    )
    assert drawn.name == "Attendance area"
    path = _write_with_private(
        tmp_path, {"outlines": {"centre": {"file": "city/core.geojson", "name": "Core"}}}
    )
    study = config.load(path)
    assert study.destinations[0].outline is None
    assert study.destinations[1].outline == config.Outline(
        file=(tmp_path / "city" / "core.geojson").resolve(), name="Core"
    )
    assert config.load(path, local=False).destinations[1].outline is None


def test_a_private_outline_must_name_a_destination_without_one(tmp_path: Path) -> None:
    path = _write_with_private(tmp_path, {"outlines": {"nowhere": {"file": "a.geojson"}}})
    with pytest.raises(config.ConfigError, match="outlines.nowhere: no destination has this id"):
        config.load(path)
    raw = copy.deepcopy(GOOD)
    raw["destinations"][0]["outline"] = {"file": "a.geojson"}
    path = tmp_path / "town.yaml"
    path.write_text(yaml.safe_dump(raw))
    (tmp_path / "town.local.yaml").write_text(
        yaml.safe_dump({"outlines": {"school": {"file": "b.geojson"}}})
    )
    with pytest.raises(config.ConfigError, match="already gives this destination an outline"):
        config.load(path)
    demo_town = copy.deepcopy(GOOD)
    demo_town["area"] = {"source": "demo"}
    demo_town["synthetic"] = True
    path.write_text(yaml.safe_dump(demo_town))
    with pytest.raises(config.ConfigError, match="the demo town takes no outlines"):
        config.load(path)
    both = changed("destinations.0.outline", {"file": "a.geojson"})
    both["destinations"][0]["serves"] = "b.geojson"
    with pytest.raises(config.ConfigError, match="give serves or a limiting outline, not both"):
        config.parse(both)
    both["destinations"][0]["outline"]["limits"] = False  # drawn only: fine with serves
    assert config.parse(both).destinations[0].outline.limits is False  # type: ignore[union-attr]
    with pytest.raises(config.ConfigError, match="limits: expected true or false"):
        config.parse(changed("destinations.0.outline", {"file": "a.geojson", "limits": "no"}))
    with pytest.raises(config.ConfigError, match="unknown setting 'colour'"):
        config.parse(changed("destinations.0.outline", {"file": "a.geojson", "colour": "red"}))


def test_a_private_file_has_no_private_file_of_its_own(tmp_path: Path) -> None:
    assert config.local_file(tmp_path / "town.yaml") == tmp_path / "town.local.yaml"
    assert config.local_file(tmp_path / "town.yml") == tmp_path / "town.local.yaml"
    assert config.local_file(tmp_path / "town.local.yaml") is None


@pytest.mark.parametrize("bad", ["../escaped", "a/b", "has space", "report.html", ""])
def test_a_destination_id_is_a_plain_folder_name(bad: str) -> None:
    with pytest.raises(config.ConfigError, match="id"):
        config.parse(changed("destinations.0.id", bad))


@pytest.mark.parametrize("reserved", ["network", "all-destinations"])
def test_a_destination_may_not_take_a_folder_the_run_writes(reserved: str) -> None:
    with pytest.raises(config.ConfigError, match="writes itself"):
        config.parse(changed("destinations.0.id", reserved))


def test_a_setting_given_twice_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "twice.yaml"
    path.write_text(yaml.safe_dump(GOOD) + "study: Somewhere else\n")
    with pytest.raises(config.ConfigError, match="'study' is set twice"):
        config.load(path)


def test_a_preset_listed_twice_is_refused() -> None:
    with pytest.raises(config.ConfigError, match="'shortest' is listed twice"):
        config.parse(changed("presets.walk", ["shortest", "shortest"]))


def test_a_band_above_the_shortest_limit_is_refused_and_left_out_of_the_defaults() -> None:
    assert config.parse(GOOD).time_bands_min == (5.0, 10.0, 15.0)  # bike stops at 15
    with pytest.raises(config.ConfigError, match=r"20 is above time_caps_min.bike \(15\)"):
        config.parse(GOOD | {"time_bands_min": [5, 20]})
    assert config.parse(GOOD | {"time_bands_min": [5, 15]}).time_bands_min == (5.0, 15.0)


def test_shorter_limits_must_increase_and_stay_under_both_active_limits() -> None:
    assert config.parse(GOOD).shorter_limits_min == ()
    assert config.parse(GOOD | {"shorter_limits_min": [5, 10]}).shorter_limits_min == (5.0, 10.0)
    with pytest.raises(config.ConfigError, match=r"15 is not below time_caps_min.bike \(15\)"):
        config.parse(GOOD | {"shorter_limits_min": [15]})
    with pytest.raises(config.ConfigError, match="must increase"):
        config.parse(GOOD | {"shorter_limits_min": [10, 5]})
    with pytest.raises(config.ConfigError, match="expected a list"):
        config.parse(GOOD | {"shorter_limits_min": 10})


def test_an_unknown_setting_that_is_not_text_is_named_not_a_crash() -> None:
    with pytest.raises(config.ConfigError, match="unknown setting"):
        raw: dict[Any, Any] = GOOD | {2026: "x", "zzz": "y"}
        config.parse(raw)


def test_files_the_config_names_are_checked_before_anything_is_fetched(tmp_path: Path) -> None:
    study = config.parse(GOOD | {"overrides": "fixes.yaml"}, base=tmp_path)
    missing = config.missing_files(study)
    assert [m.split(":")[0] for m in missing] == ["overrides", "destination 'centre' boundary_file"]
    (tmp_path / "fixes.yaml").write_text("")
    (tmp_path / "centre.geojson").write_text("{}")
    assert config.missing_files(study) == []
    from walkbike_routes import __main__ as cli

    (tmp_path / "fixes.yaml").unlink()
    with pytest.raises(config.ConfigError, match="overrides: no such file"):
        cli.build(study, tmp_path / "out", tmp_path / "data")
    assert not (tmp_path / "data").exists()  # nothing was downloaded


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        ("area", {"source": "demo", "place": "Somewhere, Oregon"}, "area.place: does not apply"),
        ("area", {"source": "demo", "buffer_m": 100}, "area.buffer_m: does not apply"),
        ("area.boundary_file", "town.geojson", "area.boundary_file: does not apply"),
        ("area", {"source": "boundary_file", "boundary_file": "t.geojson", "place": "X, Oregon"},
         "area.place: does not apply"),
        ("area", {"source": "place", "place": "X, Oregon", "buildings": "none",
                  "overture_release": "2026-10-21.0"}, "area.overture_release: does not apply"),
        ("destinations.0.boundary_file", "school.geojson", "boundary_file does not apply"),
        ("destinations.1.location", [-122.1, 37.4], "location does not apply"),
    ],
)  # fmt: skip
def test_a_setting_that_cannot_apply_is_refused(path: str, value: Any, message: str) -> None:
    with pytest.raises(config.ConfigError, match=message):
        config.parse(changed(path, value))


def test_a_yaml_merge_is_not_a_setting_given_twice(tmp_path: Path) -> None:
    path = tmp_path / "merged.yaml"
    path.write_text("a: &base {x: 1, y: 2}\nb:\n  <<: *base\n  y: 3\n")
    assert config._read_yaml(path) == {"a": {"x": 1, "y": 2}, "b": {"x": 1, "y": 3}}
    path.write_text("a: &base {x: 1}\nb:\n  <<: *base\n  y: 3\n  y: 4\n")
    with pytest.raises(config.ConfigError, match="'y' is set twice"):
        config._read_yaml(path)


def test_a_folder_dataset_counts_as_a_file_that_is_there(tmp_path: Path) -> None:
    (tmp_path / "centre.geojson").mkdir()  # as a File Geodatabase is a folder
    assert config.missing_files(config.parse(GOOD, base=tmp_path)) == []
