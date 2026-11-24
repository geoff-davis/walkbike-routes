"""Routing on the network near a destination gives the whole network's answer where it says so."""

from __future__ import annotations

import dataclasses
import logging
import math
import random
from pathlib import Path
from typing import cast

import geopandas as gpd
import networkx as nx
import pandas as pd
import pytest

from walkbike_routes import __main__ as cli
from walkbike_routes import config, demo, study_active
from walkbike_routes.analysis import screens
from walkbike_routes.models import TravelDirection
from walkbike_routes.routing import local
from walkbike_routes.routing.paths import multi_source_k_paths, multi_source_paths

DEMO = Path(__file__).resolve().parents[1] / "config" / "demo.yaml"
TO = TravelDirection.TO_DESTINATION


def _grid(seed: int, size: int = 14, step: float = 100.0) -> tuple[nx.DiGraph[str], dict]:
    """A street grid with uneven costs, some links far cheaper per metre than others."""
    rng = random.Random(seed)
    graph: nx.DiGraph[str] = nx.DiGraph()
    xy = {}
    for i in range(size):
        for j in range(size):
            xy[f"{i}-{j}"] = (i * step, j * step)
    for i in range(size):
        for j in range(size):
            for di, dj in ((1, 0), (0, 1)):
                if i + di < size and j + dj < size:
                    a, b = f"{i}-{j}", f"{i + di}-{j + dj}"
                    for u, v in ((a, b), (b, a)):
                        graph.add_edge(u, v, cost=step * rng.choice((0.6, 1.0, 1.0, 1.5, 3.0)))
    return graph, xy


@pytest.mark.parametrize("seed", range(6))
def test_certified_nodes_match_the_whole_network(seed: int) -> None:
    graph, xy = _grid(seed)
    entrance = {"gate": "3-4"}
    away = local.distances(xy, entrance.values())
    per_m = local.cost_per_metre(graph, xy, "cost")
    assert per_m == pytest.approx(0.6)
    cut = local.near(graph, away, per_m, 500.0)
    assert cut is not None and 0 < cut.graph.number_of_nodes() < graph.number_of_nodes()

    whole = multi_source_paths(graph, entrance, weight="cost", direction=TO)
    part = multi_source_paths(cut.graph, entrance, weight="cost", direction=TO)
    sure = [n for n, c in part.cost_by_node.items() if c < cut.bound(n)]
    assert len(sure) > 10
    for n in sure:
        assert part.cost_by_node[n] == whole.cost_by_node[n]
        assert part.path_by_node[n] == whole.path_by_node[n]

    gap, k = 90.0, 4
    whole_k = multi_source_k_paths(
        graph, entrance, weight="cost", direction=TO, maximum_paths=k,
        maximum_cost_gap=gap, maximum_cost_ratio=1.5,
    )  # fmt: skip
    part_k = multi_source_k_paths(
        cut.graph, entrance, weight="cost", direction=TO, maximum_paths=k,
        maximum_cost_gap=gap, maximum_cost_ratio=1.5,
    )  # fmt: skip
    best = {n: found[0].cost for n, found in part_k.alternatives_by_node.items()}
    checked = [n for n, b in best.items() if b + gap < cut.bound(n)]
    assert len(checked) > 10
    for n in checked:
        assert part_k.alternatives_by_node[n] == whole_k.alternatives_by_node[n]
    ok, _ = local.exact_alternatives(cut, {n: best[n] for n in checked}, gap)
    assert ok


def test_the_grids_include_nodes_better_reached_from_outside_the_circle() -> None:
    """So the check above is not trivially true."""
    worse = 0
    for seed in range(6):
        graph, xy = _grid(seed)
        away = local.distances(xy, ["3-4"])
        cut = local.near(graph, away, 0.6, 500.0)
        assert cut is not None
        whole = multi_source_paths(graph, {"gate": "3-4"}, weight="cost", direction=TO)
        part = multi_source_paths(cut.graph, {"gate": "3-4"}, weight="cost", direction=TO)
        worse += sum(part.cost_by_node[n] > whole.cost_by_node[n] for n in part.cost_by_node)
    assert worse > 0


def test_a_failed_check_asks_for_a_radius_that_passes() -> None:
    graph, xy = _grid(1)
    entrance = {"gate": "3-4"}
    away = local.distances(xy, entrance.values())
    per_m = local.cost_per_metre(graph, xy, "cost")
    ends = ["9-9", "10-2"]
    cut = local.near(graph, away, per_m, 1.5 * max(away[e] for e in ends))
    assert cut is not None
    tree = multi_source_paths(cut.graph, entrance, weight="cost", direction=TO)
    ok, needed = local.exact_homes(cut, tree.cost_by_node, ends, None)
    assert not ok and math.isfinite(needed) and needed > cut.radius_m
    wider = local.near(graph, away, per_m, needed)
    assert wider is not None
    tree = multi_source_paths(wider.graph, entrance, weight="cost", direction=TO)
    whole = multi_source_paths(graph, entrance, weight="cost", direction=TO)
    assert local.exact_homes(wider, tree.cost_by_node, ends, None)[0]
    assert all(tree.path_by_node[e] == whole.path_by_node[e] for e in ends)


def test_no_bound_without_a_positive_cost_per_metre() -> None:
    graph, xy = _grid(2)
    away = local.distances(xy, ["3-4"])
    graph.add_edge("0-0", "0-1", cost=-1.0)
    assert math.isnan(local.cost_per_metre(graph, xy, "cost"))
    assert local.near(graph, away, math.nan, 300.0) is None
    assert local.near(graph, away, 0.0, 300.0) is None
    assert local.near(graph, away, 1.0, 1e9) is None  # nothing is cut away


def test_an_end_that_reaches_nothing_anywhere_is_safe() -> None:
    graph, xy = _grid(3)
    graph.add_node("lone")
    xy["lone"] = (50.0, 50.0)
    away = local.distances(xy, ["3-4"])
    cut = local.near(graph, away, 0.6, 500.0)
    assert cut is not None
    assert local.exact_homes(cut, {}, ["lone"], None) == (False, math.inf)
    assert local.exact_homes(cut, {}, ["lone"], local.reaches(graph, ["3-4"]))[0]


def _served(tmp_path: Path) -> config.Study:
    """The demo, with the school taking homes only from round it: a cut is worth making."""
    school = demo.build().destinations.set_index("destination_id").geometry["school"]
    area = tmp_path / "area.geojson"
    gpd.GeoSeries([school.buffer(700)], crs=demo.CRS).to_crs(4326).to_file(area)
    study = config.load(DEMO)
    destinations = tuple(
        dataclasses.replace(d, serves=area) if d.id == "school" else d for d in study.destinations
    )
    return dataclasses.replace(study, destinations=destinations, shorter_limits_min=(5.0,))


@pytest.mark.parametrize("start", [study_active.START, 0.4])
def test_a_run_near_the_destination_writes_what_a_whole_network_run_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, start: float
) -> None:
    """Also when the first circle is too small and has to widen (``start`` 0.4)."""
    near, whole = tmp_path / "near", tmp_path / "whole"
    monkeypatch.setattr(study_active, "START", start)
    if start < 1:  # and each screen's first circle too small as well
        monkeypatch.setattr(study_active, "_first_circle", lambda *_: 150.0)
    widened = []
    real = screens.improvement_screen

    def watched(*args: object, **kwargs: object) -> object:
        try:
            return real(*args, **kwargs)  # type: ignore[arg-type]
        except screens.NotExact:
            widened.append(1)
            raise

    monkeypatch.setattr(screens, "improvement_screen", watched)
    with caplog.at_level(logging.INFO):
        cli.build(_served(tmp_path), near)
    used = [r.getMessage() for r in caplog.records if "routed within" in r.getMessage()]
    screened = [r.getMessage() for r in caplog.records if "screened within" in r.getMessage()]
    if start < 1:
        assert widened  # a screen's first circle failed its check and was widened
    else:
        for mode in ("walk", "bike"):
            assert any(m.startswith(f"school by {mode}") for m in used), mode
            assert any(m.startswith(f"school by {mode}") for m in screened), mode
    monkeypatch.setattr(study_active, "TRIES", 0)
    cli.build(_served(tmp_path), whole)
    found = sorted(p.relative_to(near) for p in near.rglob("*.parquet"))
    assert found == sorted(p.relative_to(whole) for p in whole.rglob("*.parquet"))
    for path in found:
        pd.testing.assert_frame_equal(
            pd.read_parquet(near / path), pd.read_parquet(whole / path), obj=str(path)
        )
    pd.testing.assert_frame_equal(
        pd.read_csv(near / "summary.csv"), pd.read_csv(whole / "summary.csv")
    )
    listed = [p for p in found if p.name == "screen.parquet" and p.parts[0] == "school"]
    assert listed and all(len(pd.read_parquet(near / p)) for p in listed)


def test_the_cut_keeps_each_nodes_link_order_so_ties_break_the_same_way() -> None:
    """Two equally cheap routes H-A-D and H-B-D, D's links added B first (review SC1)."""
    graph: nx.DiGraph[str] = nx.DiGraph()
    for u, v in (("H", "A"), ("H", "B"), ("B", "D"), ("A", "D")):
        graph.add_edge(u, v, cost=100.0)
    graph.add_node("far")
    xy = {"H": (0.0, 0.0), "A": (-50.0, 50.0), "B": (50.0, 50.0), "D": (0.0, 100.0),
          "far": (5000.0, 0.0)}  # fmt: skip
    away = local.distances(xy, ["D"])
    cut = local.near(graph, away, local.cost_per_metre(graph, xy, "cost"), 500.0)
    assert cut is not None and "far" not in cut.graph
    kept = cast("nx.DiGraph[str]", cut.graph)
    for n in kept:
        assert list(kept.succ[n]) == list(graph.succ[n])
        assert list(kept.pred[n]) == list(graph.pred[n])
    paths: dict[str, list[list[str]]] = {
        name: [
            cast(
                "dict[str, list[str]]",
                nx.multi_source_dijkstra(g.reverse(copy=False), ["D"], weight="cost")[1],
            )["H"]
        ]
        for name, g in (("whole", graph), ("part", kept))
    }
    whole, part = paths["whole"][0], paths["part"][0]
    assert part == whole == ["D", "B", "H"]
