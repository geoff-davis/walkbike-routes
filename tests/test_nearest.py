"""A nearest group splits its homes: each goes only to the member nearest along streets."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from walkbike_routes import __main__ as cli
from walkbike_routes import config

DEMO = Path(__file__).resolve().parents[1] / "config" / "demo.yaml"


def _config(tmp_path: Path, **changes: object) -> Path:
    raw = yaml.safe_load(DEMO.read_text())
    raw["modes"] = ["walk"]
    raw["presets"] = {"walk": ["shortest"]}
    raw["time_caps_min"] = {"walk": raw["time_caps_min"]["walk"]}
    for d in raw["destinations"]:
        d.pop("min_age", None)  # the same homes for both
        d.pop("max_age", None)
        d.update(changes)
    path = tmp_path / "study.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


def _homes(out: Path, destination: str) -> set[str]:
    routes = pd.read_parquet(next((out / destination).glob("walk-shortest/routes*.parquet")))
    return set(routes.origin_id.astype(str))


def test_each_home_goes_only_to_its_nearest_destination(tmp_path: Path) -> None:
    alone, shared = tmp_path / "alone", tmp_path / "shared"
    alone.mkdir()
    shared.mkdir()
    cli.build(config.load(_config(alone)), alone / "out")
    cli.build(config.load(_config(shared, nearest="town")), shared / "out")
    everyone = _homes(alone / "out", "school")
    school, downtown = _homes(shared / "out", "school"), _homes(shared / "out", "downtown")
    assert school and downtown and not school & downtown
    assert school | downtown == everyone


def test_a_nearest_group_needs_two_destinations(tmp_path: Path) -> None:
    raw = yaml.safe_load(_config(tmp_path).read_text())
    raw["destinations"][0]["nearest"] = "town"
    (tmp_path / "study.yaml").write_text(yaml.safe_dump(raw))
    with pytest.raises(config.ConfigError, match="nearest group 'town' has only one"):
        config.load(tmp_path / "study.yaml")
