"""The ported core, run end to end on the made-up demo town."""

from __future__ import annotations

import geopandas as gpd
import networkx as nx
import pandas as pd
import pytest

from walkbike_routes import demo
from walkbike_routes.adapters import canonical_origins
from walkbike_routes.analysis import calculate_accessibility, generalized_cost_reduction_screen
from walkbike_routes.costs import presets
from walkbike_routes.destinations import AccessPointGenerationPolicy, resolve_point_access_points
from walkbike_routes.models import Mode, TravelDirection
from walkbike_routes.network import (
    build_bicycle_links,
    build_pedestrian_links,
    build_signal_wait_inventory,
    prepare,
    routable_links_to_graph,
)
from walkbike_routes.network.bicycle import BicycleProfile
from walkbike_routes.network.pedestrian import PedestrianProfile
from walkbike_routes.network.signals import ARTERIALS

MPH = 0.44704


@pytest.fixture(scope="module")
def town() -> demo.Town:
    return demo.build()


@pytest.fixture(scope="module")
def edges(town: demo.Town) -> gpd.GeoDataFrame:
    return prepare.physical_edges(town.links)


def _links(edges: gpd.GeoDataFrame, town: demo.Town, mode: str, name: str) -> gpd.GeoDataFrame:
    signals = build_signal_wait_inventory(prepare.nodes(town.nodes), edges)
    profile = presets.load(mode, name).profile()
    if isinstance(profile, BicycleProfile):
        return build_bicycle_links(edges, profile, signal_wait_inventory=signals)
    assert isinstance(profile, PedestrianProfile)
    return build_pedestrian_links(edges, profile, signal_wait_inventory=signals)


def _to_school(
    edges: gpd.GeoDataFrame, town: demo.Town, mode: str, name: str
) -> tuple[pd.DataFrame, gpd.GeoDataFrame, nx.Graph[str], str]:
    links = _links(edges, town, mode, name)
    preset = presets.load(mode, name)
    core_mode = Mode.BICYCLE if mode == "bike" else Mode.PEDESTRIAN
    graph = routable_links_to_graph(links, directed=True, preferred_cost=preset.objective)
    nodes = prepare.nodes(town.nodes)
    nodes = nodes[nodes.node_id.isin(set(graph.nodes))]
    homes = town.homes.rename(columns={"home_id": "origin_id"})
    origins = canonical_origins(homes, weight_field="residents_5_11", weight_unit="residents 5-11")
    school = town.destinations[town.destinations.destination_id.eq("school")].copy()
    school["destination_type"] = "point"
    access = resolve_point_access_points(
        school, nodes, AccessPointGenerationPolicy(modes=(core_mode,))
    )
    attachments = _nearest_nodes(origins, nodes, core_mode)
    speed = float(preset.parameters["speed_mph"]) * MPH
    result = calculate_accessibility(
        graph,
        origins,
        attachments,
        access,
        study_id="demo",
        destination_id="school",
        profile_id=name,
        scenario_id=f"{mode}:{name}",
        mode=core_mode,
        direction=TravelDirection.TO_DESTINATION,
        route_weight=preset.objective,
        thresholds_min=(10, 20),
        time_limit_min=20,
        snap_speed_mps=speed,
    )
    (school_node,) = set(access.node_id.astype(str))
    return result.routes, links, graph, school_node


def _nearest_nodes(origins: gpd.GeoDataFrame, nodes: gpd.GeoDataFrame, mode: Mode) -> pd.DataFrame:
    """Each home at its nearest node: enough for these tests, not what a study does."""
    near = gpd.sjoin_nearest(origins[["origin_id", "geometry"]], nodes[["node_id", "geometry"]])
    near = near.sort_values(["origin_id", "node_id"]).drop_duplicates("origin_id")
    return pd.DataFrame(
        {
            "origin_id": near.origin_id.astype(str).to_numpy(),
            "mode": mode.value,
            "attached_node_id": near.node_id.astype(str).to_numpy(),
            "snap_distance_m": 0.0,
            "network_attached": True,
            "attachment_policy": "nearest",
        }
    )


def _flows(routes: pd.DataFrame, edges: gpd.GeoDataFrame) -> pd.Series:
    """Each home's weight on every street of its route."""
    used = routes[routes.route_reachable.astype(bool) & routes.within_time_limit.astype(bool)]
    totals: dict[str, float] = {}
    for ids, weight in zip(used.physical_edge_ids, used.demand_weight, strict=True):
        for edge_id in {part for part in str(ids).split(";") if part}:
            totals[edge_id] = totals.get(edge_id, 0.0) + float(weight)
    return pd.Series(totals, dtype=float).reindex(edges.physical_edge_id).fillna(0)


def test_the_demo_streets_get_the_stress_levels_their_design_implies(
    edges: gpd.GeoDataFrame,
) -> None:
    by_class = edges.groupby("highway").lts_best.agg(set).to_dict()
    assert by_class["primary"] == {4} and by_class["residential"] == {1}
    assert by_class["path"] == {1}
    lane = edges[edges.name.eq(demo.ROW_NAMES[demo.BIKE_LANE_ROW])]
    assert set(lane.lts_best) == {2}
    status = edges.set_index("physical_edge_id").pedestrian_status
    assert set(status[edges.highway.eq("residential").to_numpy()]) == {"supported", "unknown"}
    assert "problematic" in set(status[edges.highway.eq("primary").to_numpy()])


def test_every_home_reaches_the_school_by_bike_and_flow_adds_up(
    town: demo.Town, edges: gpd.GeoDataFrame
) -> None:
    routes, _, _, school_node = _to_school(edges, town, "bike", "legacy")
    assert routes.route_reachable.all() and routes.within_time_limit.all()
    flows = _flows(routes, edges)
    # Every route ends on a link touching the school's node, so their flows add up to everyone.
    ends = edges.u.eq(school_node) | edges.v.eq(school_node)
    weight = routes.loc[routes.physical_edge_ids.astype(str).ne(""), "demand_weight"].sum()
    assert flows[ends.to_numpy()].sum() == pytest.approx(weight)


def test_stress_aware_cyclists_keep_off_the_arterials(
    town: demo.Town, edges: gpd.GeoDataFrame
) -> None:
    aware = _flows(_to_school(edges, town, "bike", "legacy")[0], edges)
    shortest = _flows(_to_school(edges, town, "bike", "shortest")[0], edges)
    arterial = edges.highway.eq("primary").to_numpy()
    assert aware[arterial].sum() < 0.5 * shortest[arterial].sum()
    assert aware.sum() >= shortest.sum()  # calmer routes are no shorter


def test_walkers_never_use_the_arterial_stretch_with_no_sidewalk(
    town: demo.Town, edges: gpd.GeoDataFrame
) -> None:
    routes, links, _, _ = _to_school(edges, town, "walk", "legacy")
    no_sidewalk = set(edges.physical_edge_id[edges.pedestrian_status.eq("problematic")])
    assert no_sidewalk and not no_sidewalk & set(links.physical_edge_id)
    used = {e for ids in routes.physical_edge_ids.astype(str) for e in ids.split(";") if e}
    assert used and not used & no_sidewalk
    assert routes.route_reachable.all()


def test_the_child_walking_preset_keeps_the_stretch_with_no_sidewalk_at_a_cost(
    town: demo.Town, edges: gpd.GeoDataFrame
) -> None:
    links = _links(edges, town, "walk", "child")
    no_sidewalk = edges[edges.pedestrian_status.eq("problematic")]
    kept = links[links.physical_edge_id.isin(set(no_sidewalk.physical_edge_id))]
    assert set(kept.physical_edge_id) == set(no_sidewalk.physical_edge_id)  # nothing banned
    assert (kept.generalized_cost_s > 3.9 * kept.movement_time_s).all()  # x4 or x12 by class
    adult, child = (_to_school(edges, town, "walk", n)[0] for n in ("legacy", "child"))
    assert child.route_reachable.all()
    assert (child.network_travel_time_s >= adult.network_travel_time_s - 1e-6).all()  # slower


def test_signals_add_their_wait_to_links_entering_them(
    town: demo.Town, edges: gpd.GeoDataFrame
) -> None:
    links = _links(edges, town, "bike", "legacy")
    waited = links[links.signal_wait_s.gt(0)]
    # The demo's signals are on its arterials: riding along one waits 15 s,
    # arriving on a side street to cross it 40 s.
    assert not waited.empty and set(waited.signal_wait_s) == {15.0, 40.0}
    on_arterial = waited.physical_edge_id.map(edges.set_index("physical_edge_id").highway)
    assert (waited.signal_wait_s.eq(15.0) == on_arterial.isin(ARTERIALS)).all()
    signals = set(town.nodes.node_id[town.nodes.control.eq("signal")])
    # Each direction keeps the street's u and v, so a reverse link arrives at u.
    arrivals = [
        u if d == "reverse" else v
        for u, v, d in zip(waited.u, waited.v, waited.direction, strict=True)
    ]
    assert set(arrivals) == signals and len(waited) == 4 * len(signals)


def _school_origins(routes: pd.DataFrame) -> pd.DataFrame:
    reached = routes[routes.route_reachable].rename(columns={"attached_node_id": "node_id"})
    reached = reached.assign(weight=reached.demand_weight)
    return pd.DataFrame(reached.groupby("node_id", as_index=False)["weight"].sum())


def test_a_new_path_saves_cyclists_time_but_a_calmer_arterial_does_not(
    town: demo.Town, edges: gpd.GeoDataFrame
) -> None:
    routes, links, _, school_node = _to_school(edges, town, "bike", "legacy")
    # A candidate path diagonally across the block south-west of the school's node.
    corner = demo.node_id(demo.SCHOOL_BLOCK[0] - 1, demo.SCHOOL_BLOCK[1] - 1)
    template = links[links.lts.eq(1) & links.signal_wait_s.eq(0)].iloc[[0]]
    scale = 2**0.5  # the diagonal is sqrt(2) block lengths
    cost_columns = [c for c in links.columns if c.endswith("_s") or c == "physical_length_m"]
    added = []
    for u, v, tag in ((corner, school_node, "forward"), (school_node, corner, "backward")):
        row = template.copy()
        row[cost_columns] = row[cost_columns] * scale
        row["u"], row["v"] = u, v
        row["routable_link_id"] = f"bicycle:new-path:{tag}"
        row["physical_edge_id"] = "new-path"
        added.append(row)
    candidate = pd.concat([links.assign(baseline_available=True), *added], ignore_index=True)
    candidate["baseline_available"] = candidate.baseline_available.fillna(False).astype(bool)
    graph = routable_links_to_graph(
        gpd.GeoDataFrame(candidate, geometry="geometry", crs=links.crs),
        directed=True,
        preferred_cost="generalized_cost_s",
    )
    arterial = tuple(edges[edges.highway.eq("primary")].physical_edge_id)
    calm = BicycleProfile("calm").factor(2)  # the arterial treated to LTS 2
    result = generalized_cost_reduction_screen(
        graph,
        [school_node],
        _school_origins(routes),
        {"new path": ("new-path",), "calmer arterial": arterial, "nothing": ()},
        lambda attrs: (
            float(attrs["generalized_cost_s"])
            if attrs["physical_edge_id"] == "new-path"
            else float(attrs["travel_time_s"]) + float(attrs["movement_time_s"]) * (calm - 1)
        ),
        cost_field="generalized_cost_s",
        cost_units_per_minute=60.0,
        baseline_available_field="baseline_available",
    ).set_index("intervention_id")
    saved = result.weighted_generalized_minutes_saved
    assert saved["new path"] > 0 and result.benefiting_weight["new path"] > 0
    # On a grid a calm street the same length always runs alongside, so nobody gains.
    assert saved["calmer arterial"] == 0
    assert saved["nothing"] == 0


def test_a_screen_naming_edges_missing_from_the_graph_is_refused(
    town: demo.Town, edges: gpd.GeoDataFrame
) -> None:
    routes, _, graph, school_node = _to_school(edges, town, "bike", "legacy")
    origins = pd.DataFrame({"node_id": [routes.attached_node_id.iloc[0]], "weight": [1.0]})
    with pytest.raises(ValueError, match="'typo' names no edge in the graph"):
        generalized_cost_reduction_screen(
            graph,
            [school_node],
            origins,
            {"typo": ("n00_00-n99_99",)},
            lambda attrs: 0.0,
            cost_field="generalized_cost_s",
            cost_units_per_minute=60.0,
        )


def test_the_demo_streets_also_get_furths_levels_and_the_furth_preset_reads_them(
    edges: gpd.GeoDataFrame,
) -> None:
    by_class = edges.groupby("highway").lts_furth.agg(set).to_dict()
    assert by_class["residential"] == {1} and by_class["path"] == {1}
    assert by_class["primary"] <= {3, 4}
    assert (
        edges.lts_furth_defaults[edges.highway.eq("residential")]
        .str.contains("daily traffic")
        .all()
    )
    profile = presets.load("bike", "furth").profile()
    assert isinstance(profile, BicycleProfile) and profile.lts_field == "lts_furth"
    swapped = edges.assign(lts_furth=4, lts_best=1)  # every street LTS 4 by Furth only
    links = build_bicycle_links(swapped, profile)
    paths = set(edges.physical_edge_id[edges.highway.eq("path")])
    assert set(links.lts[~links.physical_edge_id.isin(paths)]) == {4}
