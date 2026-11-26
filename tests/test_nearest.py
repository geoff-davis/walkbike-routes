"""A nearest group splits its homes: each goes only to the member nearest along streets."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from walkbike_routes import __main__ as cli
from walkbike_routes import config
from walkbike_routes.study_nearest import choose_nearest, route_lengths

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
    written = pd.read_parquet(shared / "out" / "destinations.parquet")
    assert set(written.nearest_group) == {"town"}
    written = pd.read_parquet(alone / "out" / "destinations.parquet")
    assert set(written.nearest_group) == {""}


def test_a_nearest_group_needs_two_destinations(tmp_path: Path) -> None:
    raw = yaml.safe_load(_config(tmp_path).read_text())
    raw["destinations"][0]["nearest"] = "town"
    (tmp_path / "study.yaml").write_text(yaml.safe_dump(raw))
    with pytest.raises(config.ConfigError, match="nearest group 'town' has only one"):
        config.load(tmp_path / "study.yaml")


def _routes(snaps: dict[str, float | None]) -> pd.DataFrame:
    """Routes to one destination: each home's walk onto the network; None: unreachable."""
    return pd.DataFrame(
        {
            "origin_id": list(snaps),
            "route_reachable": [s is not None for s in snaps.values()],
            "physical_length_m": [0.0 if s is not None else float("nan") for s in snaps.values()],
            "snap_distance_m": [s if s is not None else float("nan") for s in snaps.values()],
        }
    )


def test_the_walk_onto_the_network_counts_toward_nearest() -> None:
    # A home 10 m along a 1,000 m street from A to B, with a destination at each
    # end: both routes start at an end node with no network length, so only the
    # walk along the street tells them apart (NS1). B is listed first.
    street = pd.DataFrame(
        {"B": route_lengths(_routes({"h": 990.0})), "A": route_lengths(_routes({"h": 10.0}))}
    )
    straight = pd.DataFrame({"B": [990.0], "A": [10.0]}, index=["h"])
    assert list(street.loc["h"]) == [990.0, 10.0]
    assert choose_nearest(street, straight).tolist() == ["A"]


def test_homes_no_street_reaches_go_to_the_nearest_in_a_straight_line() -> None:
    # One home reaches only B by street; another reaches neither (NS2).
    street = pd.DataFrame(
        {
            "A": route_lengths(_routes({"near": None, "cut off": None})),
            "B": route_lengths(_routes({"near": 500.0, "cut off": None})),
        },
        index=["near", "cut off"],
    )
    straight = pd.DataFrame({"A": [100.0, 50.0], "B": [400.0, 80.0]}, index=["near", "cut off"])
    assert choose_nearest(street, straight).to_dict() == {"near": "B", "cut off": "A"}


def test_a_home_goes_only_to_members_whose_area_holds_it() -> None:
    # "edge" is nearer B, but only A's area (its district, say) holds it.
    street = pd.DataFrame({"A": [900.0, 300.0, 400.0], "B": [200.0, 100.0, None]},
                          index=["edge", "inside", "far"])  # fmt: skip
    straight = pd.DataFrame({"A": [800.0, 250.0, 350.0], "B": [150.0, 90.0, 20.0]},
                            index=street.index)  # fmt: skip
    allowed = pd.DataFrame({"A": [True, True, True], "B": [False, True, True]},
                           index=street.index)  # fmt: skip
    assert choose_nearest(street, straight, allowed).to_dict() == {
        "edge": "A",
        "inside": "B",
        "far": "A",
    }
    assert choose_nearest(street, straight).to_dict()["edge"] == "B"


def test_a_nearest_group_may_also_have_an_area() -> None:
    raw = yaml.safe_load(DEMO.read_text())
    raw["area"] = {"source": "place", "place": "Anytown"}
    raw.pop("synthetic", None)
    for i, d in enumerate(raw["destinations"]):
        d["nearest"] = "district"
        d["kind"], d["location"] = "point", [-100.0 + i / 100, 40.0]
        d.pop("boundary_file", None)
    raw["destinations"][0]["outline"] = {"file": "district.geojson", "name": "District"}
    raw["destinations"][1]["serves"] = "district.geojson"
    study = config.parse(raw, base=Path("/tmp/x"))
    first, second = study.destinations[:2]
    assert first.nearest == second.nearest == "district"
    assert first.outline is not None and first.outline.limits
    assert second.serves == Path("/tmp/x/district.geojson")
