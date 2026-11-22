"""Improvement screens on a small made-up network, checked by hand."""

from __future__ import annotations

import math
from pathlib import Path

import geopandas as gpd
import networkx as nx
import pandas as pd
import pytest
from shapely.geometry import LineString, MultiLineString, Point

from walkbike_routes.analysis import places, screens
from walkbike_routes.models import Mode
from walkbike_routes.routing.costs import CostComponents
from walkbike_routes.routing.turns import Transition, sink, state

CRS = 26910


def _edges(rows: list[dict[str, object]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [{"geometry": LineString([(i, 0), (i + 1, 0)]), **row} for i, row in enumerate(rows)],
        geometry="geometry",
        crs=CRS,
    )


def test_treatments_fix_only_what_they_can() -> None:
    edges = _edges([
        {"physical_edge_id": "busy", "lts_best": 4, "bike_allowed": True, "pedestrian_status": "unknown", "ped_allowed": True},
        {"physical_edge_id": "calm", "lts_best": 2, "bike_allowed": True, "pedestrian_status": "supported", "ped_allowed": True},
        {"physical_edge_id": "freeway", "lts_best": 4, "bike_allowed": False, "pedestrian_status": "problematic", "ped_allowed": False},
    ])  # fmt: skip
    bike = screens.treat_edges(edges, screens.BIKE_LANE).set_index("physical_edge_id")
    assert bike.lts_best.to_dict() == {"busy": 1, "calm": 2, "freeway": 4}
    walk = screens.treat_edges(edges, screens.SIDEWALK).set_index("physical_edge_id")
    assert walk.pedestrian_status.to_dict() == {
        "busy": "supported", "calm": "supported", "freeway": "problematic"
    }  # fmt: skip
    assert edges.lts_best.tolist() == [4, 2, 4]  # the input is left alone
    # Furth's levels are treated on their own: "calm" is LTS 3 by Furth only.
    furth = edges.assign(lts_furth=[4, 3, 4])
    treated = screens.treat_edges(furth, screens.BIKE_LANE).set_index("physical_edge_id")
    assert treated.lts_furth.to_dict() == {"busy": 1, "calm": 1, "freeway": 4}
    assert treated.lts_best.to_dict() == {"busy": 1, "calm": 2, "freeway": 4}
    assert treated.bike_facility.to_dict()["calm"] != "track"  # facilities follow lts_best
    with pytest.raises(ValueError, match="no improvement treatment for car"):
        screens.treatments(Mode.CAR)


def test_crossing_treatments_change_only_crossings() -> None:
    edges = _edges([
        {"physical_edge_id": "walk", "pedestrian_status": "unknown"},
        {"physical_edge_id": "paint", "crossed_road": "tertiary", "crossing_marking": "unmarked", "pedestrian_status": "supported"},
        {"physical_edge_id": "lit", "crossed_road": "primary", "crossing": "traffic_signals", "pedestrian_status": "supported"},
        {"physical_edge_id": "wide", "crossed_road": "primary", "crossing_marking": "marked", "pedestrian_status": "supported"},
        {"physical_edge_id": "quiet", "crossed_road": "residential", "crossing_marking": "unmarked", "pedestrian_status": "unknown"},
    ])  # fmt: skip
    side = screens.treat_edges(edges, screens.SIDEWALK).set_index("physical_edge_id")
    assert side.pedestrian_status.to_dict()["walk"] == "supported"
    assert side.pedestrian_status.to_dict()["quiet"] == "unknown"  # a crossing is no sidewalk
    paint = screens.treat_edges(edges, screens.CROSSWALK).set_index("physical_edge_id")
    assert paint.crossing_marking.to_dict() == {
        "walk": "", "paint": "marked", "lit": "", "wide": "marked", "quiet": "marked"
    }  # fmt: skip
    assert paint.pedestrian_status.to_dict()["walk"] == "unknown"
    signal = screens.treat_edges(edges, screens.WALK_SIGNAL).set_index("physical_edge_id")
    assert signal.crossing.fillna("").to_dict() == {  # busy roads only
        "walk": "", "paint": "traffic_signals", "lit": "traffic_signals",
        "wide": "traffic_signals", "quiet": "",
    }  # fmt: skip
    assert screens.treat_edges(edges, screens.BIKE_SIGNAL).equals(edges)  # junctions only
    # The new signal's wait goes on its crossing, split over the crossing's pieces.
    links = gpd.GeoDataFrame({"physical_edge_id": ["paint", "lit", "walk"],
                          "travel_time_s": [10.0, 10.0, 10.0], "generalized_cost_s": [50.0] * 3})  # fmt: skip
    halves = signal.reset_index().assign(crossing_share=[1.0, 0.5, 1.0, 1.0, 1.0])
    waited = screens.add_signal_wait(links, edges, halves, 30.0)
    # "paint" is half of its crossing; "lit" had its signal already.
    assert waited.travel_time_s.tolist() == [25.0, 10.0, 10.0]
    assert waited.generalized_cost_s.tolist() == [65.0, 50.0, 50.0]
    # A link that crosses ("across") already pays the new signal's wait when the
    # links are rebuilt, so it is not charged again.
    crossing = halves.assign(movement_type=["along", "across", "across", "across", "across"])
    again = screens.add_signal_wait(links, edges, crossing, 30.0)
    assert again.travel_time_s.tolist() == [10.0, 10.0, 10.0]
    timed = screens.add_signal_wait(links, edges, halves, 30.0, in_cost=False)
    assert timed.travel_time_s.tolist() == [25.0, 10.0, 10.0]
    assert timed.generalized_cost_s.tolist() == [50.0, 50.0, 50.0]


def test_busy_streets_without_protection_are_treated_whatever_their_stress() -> None:
    edges = _edges([
        # LTS 2 at 25 mph, but 11,000 vehicles a day: Broach charges for that traffic.
        {"physical_edge_id": "counted", "lts_best": 2, "highway": "tertiary", "traffic_aadt": 11_000, "bike_facility": "none"},
        {"physical_edge_id": "painted", "lts_best": 2, "highway": "tertiary", "traffic_aadt": 15_000, "bike_facility": "lane"},
        {"physical_edge_id": "secondary", "lts_best": 2, "highway": "secondary", "traffic_aadt": None, "bike_facility": "none"},
        {"physical_edge_id": "quiet", "lts_best": 2, "highway": "tertiary", "traffic_aadt": 9_000, "bike_facility": "none"},
        {"physical_edge_id": "protected", "lts_best": 1, "highway": "tertiary", "traffic_aadt": 25_000, "bike_facility": "track"},
        {"physical_edge_id": "path", "lts_best": 1, "highway": "cycleway", "traffic_aadt": None, "bike_facility": "path"},
    ])  # fmt: skip
    busy = screens.busy_without_protection(edges)
    assert edges.physical_edge_id[busy].tolist() == ["counted", "painted", "secondary"]
    bike = screens.treat_edges(edges, screens.BIKE_LANE).set_index("physical_edge_id")
    assert bike.bike_facility.to_dict() == {
        "counted": "track", "painted": "track", "secondary": "track",
        "quiet": "none", "protected": "track", "path": "path",
    }  # fmt: skip
    assert bike.lts_best.to_dict()["quiet"] == 2 and bike.lts_best.to_dict()["counted"] == 1
    furth = screens.treat_edges(edges.assign(lts_furth=2), screens.BIKE_LANE)
    assert furth.set_index("physical_edge_id").lts_furth.to_dict() == {
        "counted": 1, "painted": 1, "secondary": 1, "quiet": 2, "protected": 2, "path": 2,
    }  # fmt: skip


def _links(rows: list[tuple[str, str, str, float, str]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [{"routable_link_id": f"l:{e}", "physical_edge_id": e, "u": u, "v": v, "direction": d,
          "generalized_cost_s": c, "geometry": LineString([(0, 0), (1, 0)])}
         for e, u, v, c, d in rows],
        geometry="geometry", crs=CRS,
    )  # fmt: skip


def test_the_screen_graph_marks_what_the_treatment_improves_or_opens() -> None:
    baseline = _links([("calm", "A", "B", 100, "both"), ("busy", "B", "C", 400, "forward")])
    treated = _links([("calm", "A", "B", 100, "both"), ("busy", "B", "C", 150, "forward"),
                      ("new", "C", "D", 50, "both"), ("twin", "B", "A", 10, "forward")])  # fmt: skip
    graph, improved = screens.screen_graph(baseline, treated, preferred_cost="generalized_cost_s")
    assert improved == {"l:busy", "l:new"}
    assert graph["B"]["C"]["treated_cost_s"] == 150 and graph["B"]["C"]["baseline_available"]
    assert graph["C"]["D"]["baseline_available"] is False and graph.has_edge("D", "C")
    assert graph["B"]["A"]["routable_link_id"] == "l:calm"  # the baseline link keeps its place


def test_an_opened_one_way_link_is_kept_beside_a_baseline_link_the_other_way() -> None:
    baseline = _links([("back", "D", "H", 60, "forward"), ("h1", "H", "X", 90, "forward"),
                       ("h2", "X", "D", 90, "forward")])  # fmt: skip
    treated = pd.concat([baseline, _links([("new", "H", "D", 60, "forward")])])
    graph, improved = screens.screen_graph(baseline, gpd.GeoDataFrame(treated, crs=CRS),
                                           preferred_cost="generalized_cost_s")  # fmt: skip
    assert improved == {"l:new"} and graph["H"]["D"]["routable_link_id"] == "l:new"
    assert graph["D"]["H"]["routable_link_id"] == "l:back"


def _graph() -> nx.DiGraph[str]:
    # Home H to destination D: a calm detour H-x-D (300 s) or the busy main
    # street H-m-D, two blocks: 400 s each today, 50 s each with a protected lane.
    # A missing sidewalk link H-D opens only when treated, at 200 s.
    graph: nx.DiGraph[str] = nx.DiGraph()
    for edge, u, v, cost, treated, available in [
        ("c1", "H", "x", 150, 150, True), ("c2", "x", "D", 150, 150, True),
        ("m1", "H", "m", 400, 50, True), ("m2", "m", "D", 400, 50, True),
        ("y1", "m", "y", 100, 100, True),  # a calm side street splits Main into two blocks
    ]:  # fmt: skip
        graph.add_edge(u, v, routable_link_id=f"l:{edge}", physical_edge_id=edge,
                       generalized_cost_s=cost, treated_cost_s=treated, travel_time_s=cost,
                       physical_length_m=cost, baseline_available=available)  # fmt: skip
    return graph


EDGES = _edges([
    {"physical_edge_id": e, "name": n}
    for e, n in [("c1", "Calm Lane"), ("c2", "Calm Lane"), ("m1", "Main Street"),
                 ("m2", "Main Street"), ("y1", "Side Street")]
])  # fmt: skip
ORIGINS = pd.DataFrame({"node_id": ["H"], "weight": [10.0], "snap_travel_time_s": [0.0]})


def test_one_block_alone_saves_nothing_but_the_whole_street_does() -> None:
    result = screens.improvement_screen(
        _graph(), iter(["D"]), ORIGINS, improved=frozenset({"l:m1", "l:m2"}), edges=EDGES,
        mode=Mode.BICYCLE, candidates=10, time_limit_min=None,
    )  # fmt: skip
    best = result.iloc[0]
    assert best.label == "Main Street (2 sections)"
    # 300 s today, 100 s with both blocks treated: 200 s = 3 1/3 minutes, for 10 people.
    assert best.weighted_generalized_minutes_saved == pytest.approx(10 * 200 / 60)
    assert best.benefiting_weight == 10 and best.treatment == "protected bike lane"
    # Its two blocks are folded into it; alone they save nothing, so neither is named.
    assert not result.label.str.startswith("Main Street:").any()
    assert best.best_part == "" and math.isnan(best.best_part_minutes_saved)
    assert result["rank"].tolist() == list(range(1, len(result) + 1))
    assert best.geometry.length == pytest.approx(2.0)


def test_a_link_missing_today_can_be_opened() -> None:
    graph = _graph()
    for u, v in (("H", "m"), ("m", "D")):
        graph[u][v]["treated_cost_s"] = 400  # Main Street is not part of this treatment
    graph.add_edge("H", "D", routable_link_id="l:s", physical_edge_id="s", generalized_cost_s=200,
                   treated_cost_s=200, travel_time_s=200, physical_length_m=200,
                   baseline_available=False)  # fmt: skip
    edges = pd.concat([EDGES, _edges([{"physical_edge_id": "s", "name": "Short Cut"}])])
    result = screens.improvement_screen(
        graph, ["D"], ORIGINS, improved=frozenset({"l:s"}), edges=gpd.GeoDataFrame(edges, crs=CRS),
        mode=Mode.PEDESTRIAN, candidates=10, time_limit_min=None,
    )  # fmt: skip
    assert result.label.tolist() == ["Short Cut: Calm Lane to Main Street"]
    assert result.weighted_generalized_minutes_saved.iloc[0] == pytest.approx(10 * 100 / 60)


def _crossing_at_m(u: str, v: str, w: str) -> CostComponents:
    """Riding on through m, from H to D, crosses a busy road: 250 s."""
    return CostComponents(crossing_cost_s=250.0 if (u, v, w) == ("H", "m", "D") else 0.0)


def test_movement_costs_count_in_the_screen() -> None:
    def screen(movements: Transition | None = None) -> gpd.GeoDataFrame:
        return screens.improvement_screen(
            _graph(), ["D"], ORIGINS, improved=frozenset({"l:m1", "l:m2"}), edges=EDGES,
            mode=Mode.BICYCLE, candidates=10, time_limit_min=None, movements=movements,
        )  # fmt: skip

    def small(u: str, v: str, w: str) -> CostComponents:
        return CostComponents(crossing_cost_s=50.0 if (u, v, w) == ("H", "m", "D") else 0.0)

    # On links alone, treating Main Street saves 200 s (300 s by Calm Lane, 100 s by Main).
    assert screen().weighted_generalized_minutes_saved.max() == pytest.approx(10 * 200 / 60)
    # With the crossing at m, treated Main costs 100 + 250 = 350 s, more than Calm
    # Lane's 300 s: nobody would use it, so it is not listed at all.
    assert screen(_crossing_at_m).empty
    # A smaller crossing still leaves a saving, net of it: 300 - (100 + 50) = 150 s.
    assert screen(small).weighted_generalized_minutes_saved.max() == pytest.approx(10 * 150 / 60)


def test_the_movement_screen_graph_adds_movement_costs_to_both_costs() -> None:
    graph = screens.movement_screen_graph(_graph(), _crossing_at_m)
    through = graph[state("H", "m")][state("m", "D")]
    assert through["generalized_cost_s"] == 400 + 250 and through["treated_cost_s"] == 50 + 250
    assert through["physical_edge_id"] == "m2" and through["travel_time_s"] == 400
    to_sink = graph[state("m", "D")][sink("D")]
    assert to_sink["baseline_available"] and to_sink["treated_cost_s"] == 0
    assert "physical_edge_id" not in to_sink


def test_the_time_cap_and_candidate_count_limit_the_screen() -> None:
    args = {"improved": frozenset({"l:m1", "l:m2"}), "edges": EDGES, "mode": Mode.BICYCLE}
    capped = screens.improvement_screen(_graph(), ["D"], ORIGINS, candidates=10,
                                        time_limit_min=4, **args)  # type: ignore[arg-type]  # fmt: skip
    # Today the best route takes 5 minutes, over the cap: nobody counted, nothing saved,
    # so nothing is listed rather than a list of places saving 0.
    assert capped.empty
    one = screens.improvement_screen(_graph(), ["D"], ORIGINS, candidates=1,
                                     time_limit_min=None, **args)  # type: ignore[arg-type]  # fmt: skip
    assert len(one) == 1
    none = screens.improvement_screen(_graph(), ["D"], ORIGINS, candidates=0,
                                      time_limit_min=None, **args)  # type: ignore[arg-type]  # fmt: skip
    assert none.empty and list(none.columns) == screens.SCREEN_COLUMNS
    listed = screens.improvement_screen(_graph(), ["D"], ORIGINS, candidates=10,
                                        time_limit_min=None, **args)  # type: ignore[arg-type]  # fmt: skip
    assert listed.weighted_generalized_minutes_saved.round(1).gt(0).all()
    assert listed["rank"].tolist() == list(range(1, len(listed) + 1))
    nothing = screens.improvement_screen(_graph(), ["D"], ORIGINS, candidates=5, time_limit_min=None,
                                         improved=frozenset(), edges=EDGES, mode=Mode.BICYCLE)  # fmt: skip
    assert nothing.empty


def test_each_place_is_ranked_once_with_its_best_part() -> None:
    table = pd.DataFrame({
        "label": ["Main (3 sections)", "Main: 1st to 2nd", "Oak: A to B", "Main: 2nd to 3rd", "Elm"],
        "physical_edge_ids": ["a;b;c", "b", "x", "c", "a;y"],
        "weighted_generalized_minutes_saved": [90.0, 40.0, 30.0, 20.0, 10.0],
    }, index=[7, 3, 5, 1, 2])  # fmt: skip
    folded = screens.fold_overlaps(table)
    assert folded.label.tolist() == ["Main (3 sections)", "Oak: A to B"]  # Elm shares link a
    assert folded.best_part.tolist() == ["Main: 1st to 2nd", ""]
    assert folded.best_part_minutes_saved.iloc[0] == 40.0
    assert math.isnan(folded.best_part_minutes_saved.iloc[1])


def test_whole_streets_join_named_sections_only() -> None:
    sections = pd.DataFrame(
        {
            "section_id": ["S1", "S2", "S3", "S4", "S5"],
            "name": ["Oak Street", "Oak Street", "unnamed footway", "unnamed footway", "Elm"],
            "label": [""] * 5,
            "physical_edge_ids": ["a;b", "c", "d", "e", "f"],
            "edge_count": [2, 1, 1, 1, 1],
            "length_m": [200.0, 100.0, 50.0, 50.0, 80.0],
        }
    )
    streets = screens.whole_streets(sections)
    assert streets.to_dict("records") == [
        {"section_id": "WS1+S2", "name": "Oak Street", "label": "Oak Street (2 sections)",
         "physical_edge_ids": "a;b;c", "edge_count": 3, "length_m": 300.0}
    ]  # fmt: skip


def test_unnamed_streets_are_labelled_by_road_type() -> None:
    edges = _edges([{"physical_edge_id": "a", "name": None, "highway": "living_street"},
                    {"physical_edge_id": "b", "name": " Oak ", "highway": "residential"},
                    {"physical_edge_id": "c", "name": "", "highway": None}])  # fmt: skip
    assert places.edge_names(edges) == {
        "a": "unnamed living street",
        "b": "Oak",
        "c": "unnamed link",
    }


def test_candidates_come_from_homes_within_the_limit_only() -> None:
    args = {"improved": frozenset({"l:m1", "l:m2"}), "edges": EDGES, "mode": Mode.BICYCLE,
            "candidates": 10}  # fmt: skip
    inside = ORIGINS.assign(within_limit=True)
    found = screens.improvement_screen(_graph(), ["D"], inside, time_limit_min=10, **args)  # type: ignore[arg-type]
    assert "Main Street (2 sections)" in set(found.label)
    # A home beyond the limit today proposes no sections: they would save nothing.
    outside = ORIGINS.assign(within_limit=False)
    assert screens.improvement_screen(_graph(), ["D"], outside, time_limit_min=10, **args).empty  # type: ignore[arg-type]
    # Without a limit, everyone who can get there counts.
    free = screens.improvement_screen(_graph(), ["D"], outside, time_limit_min=None, **args)  # type: ignore[arg-type]
    assert "Main Street (2 sections)" in set(free.label)


def test_only_homes_within_the_limit_propose_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    real = screens.treated_flows

    def spy(
        graph: nx.DiGraph[str], destinations: list[str], origins: pd.DataFrame
    ) -> dict[str, float]:
        seen.append(sorted(origins.node_id))
        return real(graph, destinations, origins)

    monkeypatch.setattr(screens, "treated_flows", spy)
    origins = pd.DataFrame({"node_id": ["H", "m"], "weight": [10.0, 500.0],
                            "snap_travel_time_s": [0.0, 0.0], "within_limit": [True, False]})  # fmt: skip
    args = {"improved": frozenset({"l:m1", "l:m2"}), "edges": EDGES, "mode": Mode.BICYCLE,
            "candidates": 10}  # fmt: skip
    screens.improvement_screen(_graph(), ["D"], origins, time_limit_min=10, **args)  # type: ignore[arg-type]
    screens.improvement_screen(_graph(), ["D"], origins, time_limit_min=None, **args)  # type: ignore[arg-type]
    assert seen == [["H"], ["H", "m"]]


def test_a_junction_treatment_is_ranked_as_one_place() -> None:
    # A signal at m would remove the 250 s crossing there: Main then costs 400 + 400
    # + 13 = 813 s, against 300 s by Calm Lane today, so nobody gains.  With the lane
    # links already cheap (treated_cost_s), it is the crossing alone that matters.
    graph = _graph()
    for edge in ("m1", "m2"):
        u, v = ("H", "m") if edge == "m1" else ("m", "D")
        graph[u][v]["generalized_cost_s"] = graph[u][v]["treated_cost_s"] = 50

    def signal(u: str, v: str, w: str) -> float | None:
        return 13.0 if (u, v, w) == ("H", "m", "D") else None

    search = screens.movement_screen_graph(graph, _crossing_at_m, signal)
    through = search[state("H", "m")][state("m", "D")]
    assert through["generalized_cost_s"] == 50 + 250 and through["treated_cost_s"] == 50 + 13
    assert through["intervention_key"] == "J:m"
    edges = EDGES.assign(u=["H", "x", "H", "m", "m"], v=["x", "D", "m", "D", "y"])
    result = screens.improvement_screen(
        graph, ["D"], ORIGINS, improved=frozenset(), edges=edges, mode=Mode.BICYCLE,
        candidates=5, time_limit_min=None, movements=_crossing_at_m,
        treatment=screens.BIKE_SIGNAL, junction=signal,
    )  # fmt: skip
    assert len(result) == 1
    best = result.iloc[0]
    # Today 300 s (Calm Lane; Main is 350); with the signal Main is 113 s.
    assert best.weighted_generalized_minutes_saved == pytest.approx(10 * (300 - 113) / 60)
    assert (
        best.label == "Main Street and Side Street"
        and best.treatment == "signal or crossing beacon"
    )
    assert best.geometry.geom_type == "Point"
    # Without a junction cost, a junction-only treatment has nothing to try.
    assert screens.improvement_screen(
        graph, ["D"], ORIGINS, improved=frozenset(), edges=edges, mode=Mode.BICYCLE,
        candidates=5, time_limit_min=None, movements=_crossing_at_m, treatment=screens.BIKE_SIGNAL,
    ).empty  # fmt: skip


def test_a_whole_street_wins_a_tie_with_its_one_useful_section() -> None:
    # Main's first block is cheap already; only the second gains from a lane, so
    # the whole street saves exactly what that block does.  It is listed as the street.
    graph = _graph()
    graph["H"]["m"]["generalized_cost_s"] = graph["H"]["m"]["treated_cost_s"] = 100
    result = screens.improvement_screen(
        graph, iter(["D"]), ORIGINS, improved=frozenset({"l:m1", "l:m2"}), edges=EDGES,
        mode=Mode.BICYCLE, candidates=10, time_limit_min=None,
    )  # fmt: skip
    assert result.label.tolist()[0] == "Main Street (2 sections)"
    assert "Main Street" not in " ".join(result.label.tolist()[1:])
    best = result.iloc[0]
    assert best.best_part != "" and best.best_part_minutes_saved == pytest.approx(
        best.weighted_generalized_minutes_saved
    )


def test_crossing_pieces_of_two_ways_are_one_place_and_multipart_edges_are_fine() -> None:
    edges = _edges([
        {"physical_edge_id": "w1-0", "crossed_road": "primary", "crossing_group": "w1-0"},
        {"physical_edge_id": "w2-0", "crossed_road": "primary", "crossing_group": "w1-0"},
        {"physical_edge_id": "w3-1", "crossed_road": "primary", "crossing_group": ""},
        {"physical_edge_id": "main", "name": "Main Street"},
    ])  # fmt: skip
    shapes = list(edges.geometry)
    shapes[3] = MultiLineString([[(3, 0), (4, 0)], [(4, 0), (5, 1)]])
    edges = edges.set_geometry(gpd.GeoSeries(shapes, index=edges.index, crs=CRS))
    found = places.Places(edges)
    assert found.crossing_of["w1-0"] == found.crossing_of["w2-0"] == "w1-0"
    assert found.crossing_of["w3-1"] == "w3"  # no group: the way it is a piece of


def test_one_junction_drawn_as_two_nodes_is_one_place() -> None:
    rows = [  # Main runs j1-j2-mid-j3; Oak crosses at j1, j2 (a divided road) and j3
        ("m1", "Main Street", "j1", "j2", (0, 0), (20, 0)),
        ("m2", "Main Street", "j2", "mid", (20, 0), (250, 0)),
        ("m3", "Main Street", "mid", "j3", (250, 0), (500, 0)),
        ("o1", "Oak", "o", "j1", (0, -100), (0, 0)),
        ("o2", "Oak", "j2", "p", (20, 0), (20, 100)),
        ("o3", "Oak", "j3", "q", (500, 0), (500, 100)),
        ("e1", "Elm", "e", "f", (240, 30), (260, 30)),
    ]
    edges = gpd.GeoDataFrame(
        {"physical_edge_id": [r[0] for r in rows], "name": [r[1] for r in rows],
         "u": [r[2] for r in rows], "v": [r[3] for r in rows]},
        geometry=[LineString([r[4], r[5]]) for r in rows], crs=CRS,
    )  # fmt: skip
    found = places.Places(edges)
    flow = {"J:j1": 5.0, "J:j2": 3.0, "J:j3": 1.0, "J:mid": 2.0, "l:x": 9.0}
    assert places.junction_groups(found, flow) == [
        ("Main Street and Oak", ["J:j1", "J:j2"]),  # 20 m apart: one junction
        ("Main Street near Elm", ["J:mid"]),  # only Main meets here
        ("Main Street and Oak", ["J:j3"]),  # 500 m away: another
    ]


def test_a_node_between_two_others_joins_them_whatever_the_order() -> None:
    rows = [
        ("m1", "Main Street", "a", "b", (0, 0), (50, 0)),
        ("m2", "Main Street", "b", "c", (50, 0), (100, 0)),
    ]
    rows += [
        (f"o{n}", "Oak", n, f"{n}x", (x, 0), (x, 100)) for n, x in (("a", 0), ("b", 50), ("c", 100))
    ]
    edges = gpd.GeoDataFrame(
        {"physical_edge_id": [r[0] for r in rows], "name": [r[1] for r in rows],
         "u": [r[2] for r in rows], "v": [r[3] for r in rows]},
        geometry=[LineString([r[4], r[5]]) for r in rows], crs=CRS,
    )  # fmt: skip
    found = places.Places(edges)
    for flow in ({"J:a": 3.0, "J:c": 2.0, "J:b": 1.0}, {"J:b": 3.0, "J:a": 2.0, "J:c": 1.0}):
        groups = places.junction_groups(found, flow)
        assert len(groups) == 1 and sorted(groups[0][1]) == ["J:a", "J:b", "J:c"]


def test_the_screen_report_says_how_many_people_each_change_helps() -> None:
    from walkbike_routes.report.tables import screen_table

    screen = pd.DataFrame(
        {
            "rank": [2, 1, 1], "label": ["Oak: A to B", "Main Street", "Elm and 1st"],
            "treatment": ["protected bike lane"] * 2 + ["marked crosswalk"],
            "benefiting_weight": [1.0, 3.0, 0.2], "analyzed_weight": [600.0] * 3,
            "weighted_generalized_minutes_saved": [0.5, 3.6, 0.1],
            "best_part": [""] * 3, "best_part_minutes_saved": [math.nan] * 3,
        }
    )  # fmt: skip
    table = screen_table(screen)
    assert table.header[3:5] == ("People helped", "Share of those counted")
    # Each kind of change by its own rank; a part of one person is not "0".
    assert [row[:5] for row in table.rows] == [
        ("protected bike lane", "1", "Main Street", "3", "under 1%"),
        ("protected bike lane", "2", "Oak: A to B", "1", "under 1%"),
        ("marked crosswalk", "1", "Elm and 1st", "fewer than 1", "under 1%"),
    ]
    unknown = screen_table(screen.assign(analyzed_weight=math.nan))
    assert {row[4] for row in unknown.rows} == {"–"}
    assert len(screen_table(screen, 1).rows) == 2  # the best one of each kind
    assert "Households helped" in screen_table(screen, unit="households").header
    assert "Expected trips helped" in screen_table(screen, is_decayed=True).header


def test_the_map_prints_a_part_of_one_person_helped_as_the_report_does(tmp_path: Path) -> None:
    from pyproj import Transformer

    from walkbike_routes.report.map import _screen

    path = tmp_path / "screen.parquet"
    gpd.GeoDataFrame(
        {
            "rank": [1], "label": ["Elm and 1st"], "treatment": ["marked crosswalk"],
            "benefiting_weight": [0.2], "analyzed_weight": [600.0],
            "weighted_generalized_minutes_saved": [0.1],
        },
        geometry=[Point(500000, 4100000)], crs="EPSG:26910",
    ).to_parquet(path)  # fmt: skip
    to_degrees = Transformer.from_crs("EPSG:26910", "EPSG:4326", always_xy=True)
    (row,) = _screen(path, to_degrees, lambda p: True)
    assert row["helped_text"] == "fewer than 1" and row["counted_text"] == "600"


def test_the_map_works_its_shares_from_unrounded_weights() -> None:
    from walkbike_routes.report.map import _finite, _share

    assert _share(0.06, 0.14) == 43  # not 100, as 0.1 of 0.1 would give
    assert _share(3, 600) == 0.5
    assert _share(1, 0) is None and _share(1, math.nan) is None
    assert _finite(math.nan, 1) is None and _finite(None, 1) is None
