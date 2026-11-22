"""The made-up demo town and the end-to-end run on it."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import geopandas as gpd
import pytest

from walkbike_routes import __main__ as cli
from walkbike_routes import config, demo

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def town() -> demo.Town:
    return demo.build()


def test_the_streets_form_one_connected_grid(town: demo.Town) -> None:
    import networkx as nx

    links = town.links
    assert len(links) == 2 * demo.SIZE * (demo.SIZE - 1) and links.link_id.is_unique
    graph = nx.Graph(zip(links.u, links.v, strict=True))
    assert nx.is_connected(graph) and graph.number_of_nodes() == demo.SIZE**2
    assert set(graph.nodes) == set(town.nodes.node_id)
    assert (links.length.round(6) == demo.SPACING_M).all()


def test_the_town_has_the_features_the_tests_rely_on(town: demo.Town) -> None:
    links, nodes = town.links, town.nodes.set_index("node_id")
    assert set(links.highway) == {"primary", "tertiary", "residential", "path"}
    assert set(links[links.bike_facility == "lane"].name) == {demo.ROW_NAMES[demo.BIKE_LANE_ROW]}
    assert set(links.sidewalk) == {"both", "unknown", "none", "path"}
    a = demo.ARTERIAL
    assert nodes.control[demo.node_id(a, a)] == "signal"  # the two arterials
    assert nodes.control[demo.node_id(a, demo.COLLECTORS[0])] == "signal"
    assert nodes.control[demo.node_id(demo.COLLECTORS[0], demo.COLLECTORS[1])] == "stop"
    assert nodes.control[demo.node_id(1, demo.COLLECTORS[0])] == "stop"
    assert nodes.control[demo.node_id(1, 1)] == "none"
    assert nodes.control[demo.node_id(a, demo.PATH_COLUMN)] == "none"  # path meets arterial
    top = nodes.elevation_m[demo.node_id(*demo.HILL)]
    assert top == nodes.elevation_m.max() and top - nodes.elevation_m.min() > 20


def test_homes_avoid_the_school_and_downtown(town: demo.Town) -> None:
    downtown = town.destinations.set_index("destination_id").geometry["downtown"]
    assert not town.homes.within(downtown).any()
    assert town.homes.home_id.is_unique and (town.homes.residents_5_11 >= 0).all()
    gates = town.access_points
    assert set(gates.destination_id) == {"school"} and len(gates) == 2


def test_the_same_seed_gives_the_same_town() -> None:
    first, again, other = demo.build(), demo.build(), demo.build(seed=1)
    assert first.homes.equals(again.homes)
    assert not first.homes.residents_5_11.equals(other.homes.residents_5_11)


def test_the_run_writes_every_layer_and_a_manifest(tmp_path: Path) -> None:
    cli.main(["run", "--config", str(ROOT / "config" / "demo.yaml"), "--out", str(tmp_path)])
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["synthetic"] is True and manifest["crs"] == demo.CRS
    assert {f"{n}.parquet" for n in cli.LAYERS} <= set(manifest["files"])
    assert "summary.csv" in manifest["files"]
    links = gpd.read_parquet(tmp_path / "links.parquet")
    assert links.crs == demo.CRS and len(links) == len(demo.build().links)


def test_the_run_writes_layers_in_the_chosen_crs(tmp_path: Path) -> None:
    study = replace(config.load(ROOT / "config" / "demo.yaml"), crs="EPSG:5070")
    manifest = json.loads(cli.build(study, tmp_path).read_text())
    assert manifest["crs"] == "EPSG:5070"
    assert gpd.read_parquet(tmp_path / "homes.parquet").crs == "EPSG:5070"


def test_the_run_refuses_what_it_cannot_build(tmp_path: Path) -> None:
    study = config.load(ROOT / "config" / "demo.yaml")
    with pytest.raises(config.ConfigError, match="remove synthetic: true"):
        cli.build(replace(study, area=config.Area(source="place", place="X, Ohio")), tmp_path)
    with pytest.raises(config.ConfigError, match="synthetic: true"):
        cli.build(replace(study, synthetic=False), tmp_path)
    odd = (config.Destination(id="park", name="Park", kind="point"),)
    with pytest.raises(config.ConfigError, match="'park' is not in the demo town"):
        cli.build(replace(study, destinations=odd), tmp_path)


def test_a_bad_config_exits_with_the_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("study: X\n")
    with pytest.raises(SystemExit) as stop:
        cli.main(["run", "--config", str(bad), "--out", str(tmp_path)])
    assert stop.value.code == 2 and "missing setting" in capsys.readouterr().err
