"""Grouping barrier links into same-street sections."""

from __future__ import annotations

import math

import networkx as nx
import pytest

from walkbike_routes import demo
from walkbike_routes.analysis.barriers import (
    barrier_sections,
    clean_text,
    link_length,
)
from walkbike_routes.analysis.places import section_label
from walkbike_routes.network import build_bicycle_links, prepare, routable_links_to_graph
from walkbike_routes.network.bicycle import BicycleProfile


def _busy_road() -> nx.Graph[str]:
    """Busy Rd runs a-b-c-d-e; Calm St crosses at c, Quiet Ln ends at e."""
    graph: nx.Graph[str] = nx.Graph()
    for u, v in (("a", "b"), ("b", "c"), ("c", "d"), ("d", "e")):
        graph.add_edge(u, v, edge_id=f"{u}{v}", name="Busy Rd", busy=True, length_m=100.0)
    graph.add_edge("c", "x", edge_id="cx", name="Calm St", busy=False, length_m=80.0)
    graph.add_edge("e", "y", edge_id="ey", name="Quiet Ln", busy=False, length_m=80.0)
    graph.add_edge("b", "z", edge_id="bz", name="Busy Rd Spur", busy=True, length_m=50.0)
    return graph


def test_a_comfortable_crossing_splits_a_busy_street() -> None:
    by_edge, sections = barrier_sections(_busy_road(), lambda attrs: bool(attrs["busy"]))
    busy = sections[sections.name.eq("Busy Rd")].set_index("physical_edge_ids")
    assert list(busy.index) == ["ab;bc", "cd;de"]
    assert busy.loc["cd;de", "label"] == "Busy Rd: Calm St to Quiet Ln"
    assert busy.loc["ab;bc", "label"] == "Busy Rd near Calm St"
    assert busy.length_m.tolist() == [200.0, 200.0]
    # The spur meets Busy Rd at b but has another name, so it stays apart.
    spur = sections[sections.name.eq("Busy Rd Spur")]
    assert spur.physical_edge_ids.tolist() == ["bz"] and spur.label.tolist() == ["Busy Rd Spur"]
    assert by_edge["ab"] == by_edge["bc"] != by_edge["cd"]


def test_a_physical_id_function_merges_directions() -> None:
    graph: nx.DiGraph[str] = nx.DiGraph()
    graph.add_edge("a", "b", edge_id="ab:f", name="", length_m=10.0)
    graph.add_edge("b", "a", edge_id="ab:r", name="", length_m=10.0)
    by_edge, sections = barrier_sections(
        graph, lambda attrs: True, physical_id=lambda raw: raw.split(":")[0]
    )
    assert sections.physical_edge_ids.tolist() == ["ab"] and sections.label.tolist() == [
        "Unnamed link"
    ]
    assert by_edge == {"ab:f": sections.section_id[0], "ab:r": sections.section_id[0]}


def test_the_demo_arterials_split_into_blocks_at_each_calm_street() -> None:
    town = demo.build()
    edges = prepare.physical_edges(town.links)
    links = build_bicycle_links(edges, BicycleProfile("all"))
    graph = routable_links_to_graph(links, directed=True)
    names = dict(zip(edges.physical_edge_id, edges.name, strict=True))
    _, sections = barrier_sections(
        graph, lambda attrs: int(attrs["lts"]) >= 4, name_of=lambda pid, _attrs: names[pid]
    )
    arterials = {demo.ROW_NAMES[demo.ARTERIAL], demo.COLUMN_NAMES[demo.ARTERIAL]}
    assert set(sections.name) == arterials
    # A calm street meets every node but the centre, where only the arterials
    # cross; so each block is its own section except the two either side of it.
    assert len(sections) == 2 * (demo.SIZE - 2)
    centre = demo.node_id(demo.ARTERIAL, demo.ARTERIAL)
    joined = sections[sections.edge_count.eq(2)]
    assert len(joined) == 2 and all(centre in ids for ids in joined.physical_edge_ids)
    assert sections.length_m.sum() == pytest.approx(2 * (demo.SIZE - 1) * demo.SPACING_M)


def test_text_and_length_helpers() -> None:
    assert [clean_text(v) for v in (None, math.nan, " NaN ", "<NA>", " Elm ")] == [
        "",
        "",
        "",
        "",
        "Elm",
    ]
    assert link_length({"physical_length_m": 5, "length_m": 9}) == 5
    assert link_length({"length_m": 9}) == 9
    assert section_label("", []) == "Unnamed link"


def test_links_with_no_physical_edge_stay_apart() -> None:
    graph: nx.DiGraph[str] = nx.DiGraph()
    graph.add_edge("a", "b", routable_link_id="x1", physical_edge_id=None, length_m=10.0)
    graph.add_edge("c", "d", routable_link_id="x2", physical_edge_id=None, length_m=10.0)
    by_edge, sections = barrier_sections(graph, lambda attrs: True)
    assert len(sections) == 2 and sections.length_m.sum() == 20.0
    assert by_edge["x1"] != by_edge["x2"]
