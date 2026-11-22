"""Movement-specific signal events for pedestrian and bicycle routing."""

from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass
from typing import Any

import geopandas as gpd
import networkx as nx
import pandas as pd

from walkbike_routes.attributes import NONMOTOR_HIGHWAYS

SIGNAL_CLUSTER_DISTANCE_M = 35.0
SIGNAL_CLUSTER_MAX_DIAMETER_M = 70.0
SIGNAL_CLUSTER_FOOTPRINT_BUFFER_M = 20.0

_SIGNAL_STATES = frozenset({"signal_local", "signal_osm"})
_SIGNAL_CROSSING_TAGS = frozenset({"traffic_signals"})
# Main roads, whose through traffic a signal usually serves with the longer green.
ARTERIALS = frozenset(
    {"trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link"}
)


class SignalWait:
    """Which wait a movement through a signal pays."""

    CROSS_ARTERIAL = "cross_arterial"  # across a main road: its short walk or side-street phase
    ALONG_ARTERIAL = "along_arterial"  # along a main road: with its long green
    OTHER = "other"  # no main road at the signal, or not known


@dataclass(frozen=True)
class SignalWaitInventory:
    """Stable signal-cluster and explicit-crossing event lookup.

    ``cluster_by_node`` supports roadway bicycle movements: a wait is charged
    when a directed link enters a physical signal-cluster footprint. Explicit
    pedestrian crossing geometries are often split into several links, so
    ``crossing_event_by_wait_edge`` identifies one central link per connected
    crossing leg and prevents duplicate waits. ``crossing_leg_edges`` lists
    every link of a signalized crossing so its non-central legs never pay a
    cluster entry on top of the crossing event.  ``clusters_with_crossings``
    are the clusters that have a mapped signalized crossing; walkers pay at
    those crossings and so are not charged on entering such a cluster.
    """

    cluster_by_node: dict[str, str]
    crossing_event_by_wait_edge: dict[str, str]
    crossing_leg_edges: frozenset[str] = frozenset()
    clusters_with_crossings: frozenset[str] = frozenset()
    arterial_clusters: frozenset[str] = frozenset()
    crossing_nodes: frozenset[str] = frozenset()

    def event_for_link(
        self,
        *,
        u: str,
        v: str,
        physical_edge_id: str,
        include_roadway_signal_entries: bool,
        skip_clusters_with_crossings: bool = False,
        charge_inside_crossing_clusters: bool = False,
    ) -> str | None:
        """Return the one signal event charged on a directed routable link.

        With ``skip_clusters_with_crossings``, arriving at the end of a mapped
        signalized crossing charges nothing: the crossing link does.  Arriving
        elsewhere in the intersection still charges.
        With ``charge_inside_crossing_clusters``, a link inside such a cluster
        is charged too: walking the carriageway through an intersection whose
        crosswalks are mapped is crossing it without them.
        """
        edge_id = str(physical_edge_id)
        crossing_event = self.crossing_event_by_wait_edge.get(edge_id)
        if crossing_event is not None:
            return crossing_event
        if edge_id in self.crossing_leg_edges or not include_roadway_signal_entries:
            return None
        target_cluster = self.cluster_by_node.get(str(v))
        if target_cluster is None:
            return None
        if self.cluster_by_node.get(str(u)) == target_cluster:
            inside_charged = (
                charge_inside_crossing_clusters and target_cluster in self.clusters_with_crossings
            )
            return target_cluster if inside_charged else None
        if skip_clusters_with_crossings and str(v) in self.crossing_nodes:
            return None
        return target_cluster

    def wait_kind(
        self, event_id: str, *, u: str, v: str, highway: str, crossing_road: str = ""
    ) -> str:
        """The :class:`SignalWait` kind for a charged movement.

        A crossing link crossing a main road waits for the side phase; one
        crossing a side road at a main-road signal goes with the main road's
        green.  Entering a main-road signal along a side road is charged as
        crossing the main road, along the main road as going with its green:
        a link does not know whether the walker or rider turns there.
        """
        clusters = {self.cluster_by_node.get(str(u)), self.cluster_by_node.get(str(v))}
        at_arterial = bool(clusters & self.arterial_clusters)
        if event_id in self.crossing_event_by_wait_edge.values():
            if crossing_road in ARTERIALS:
                return SignalWait.CROSS_ARTERIAL
            return SignalWait.ALONG_ARTERIAL if crossing_road and at_arterial else SignalWait.OTHER
        if event_id not in self.arterial_clusters:
            return SignalWait.OTHER
        return SignalWait.ALONG_ARTERIAL if highway in ARTERIALS else SignalWait.CROSS_ARTERIAL


class _UnionFind:
    def __init__(self, values: list[str]):
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> str:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return left_root
        low, high = sorted((left_root, right_root))
        self.parent[high] = low
        return low


def _signal_crossing_edges(physical_edges: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    movement = physical_edges.get(
        "movement_type", pd.Series("along", index=physical_edges.index)
    ).astype(str)
    state = (
        physical_edges.get(
            "crossing_control_state", pd.Series("unknown", index=physical_edges.index)
        )
        .astype(str)
        .str.lower()
    )
    crossing = (
        physical_edges.get("crossing", pd.Series("", index=physical_edges.index))
        .astype(str)
        .str.lower()
    )
    selected = movement.eq("across") & (
        state.isin(_SIGNAL_STATES) | crossing.isin(_SIGNAL_CROSSING_TAGS)
    )
    return physical_edges[selected].copy()


def _crossing_events(
    physical_edges: gpd.GeoDataFrame,
) -> tuple[dict[str, str], set[str], set[str]]:
    """Return the central wait edge per crossing leg, interior nodes, and all leg edges."""
    signal_edges = _signal_crossing_edges(physical_edges)
    if signal_edges.empty:
        return {}, set(), set()
    signal_edges["physical_edge_id"] = signal_edges.physical_edge_id.astype(str)
    leg_edges = set(signal_edges.physical_edge_id)
    source_field = (
        "source_network_id" if "source_network_id" in signal_edges else "physical_edge_id"
    )
    # A crossing drawn as several ways is one crossing (``prepare`` joins them
    # as a ``crossing_group``): one wait, kerb to kerb.
    crossing = signal_edges[source_field].astype(str)
    if "crossing_group" in signal_edges:
        group = signal_edges.crossing_group.fillna("").astype(str)
        crossing = group.where(group.ne(""), crossing)
    wait_edges: dict[str, str] = {}
    interior_nodes: set[str] = set()
    for _source_id, source_edges in signal_edges.groupby(crossing, sort=True):
        # A multigraph keeps parallel crossing legs between the same node pair.
        graph: nx.MultiGraph[str] = nx.MultiGraph()
        row_by_id: dict[str, Any] = {}
        for row in source_edges.itertuples(index=False):
            edge_id = str(row.physical_edge_id)
            graph.add_edge(str(row.u), str(row.v), key=edge_id)
            row_by_id[edge_id] = row
        for component in nx.connected_components(graph):
            component_graph = graph.subgraph(component)
            component_edges = sorted(str(key) for _, _, key in component_graph.edges(keys=True))
            if not component_edges:
                continue
            digest = hashlib.sha1(
                ";".join(sorted(component_edges)).encode(), usedforsecurity=False
            ).hexdigest()[:12]
            event_id = f"signal_crossing_{digest}"
            union = source_edges[
                source_edges.physical_edge_id.isin(component_edges)
            ].geometry.union_all()
            center = union.centroid
            selected_edge = min(
                component_edges,
                key=lambda edge_id: (
                    row_by_id[edge_id].geometry.interpolate(0.5, normalized=True).distance(center),
                    edge_id,
                ),
            )
            wait_edges[selected_edge] = event_id
            candidates = [node for node, degree in component_graph.degree if degree > 1]
            if not candidates:
                candidates = [
                    min(
                        component,
                        key=lambda node: (
                            min(
                                row_by_id[edge_id].geometry.distance(center)
                                for edge_id in component_edges
                                if node
                                in {
                                    str(row_by_id[edge_id].u),
                                    str(row_by_id[edge_id].v),
                                }
                            ),
                            node,
                        ),
                    )
                ]
            interior_nodes.update(str(value) for value in candidates)
    return wait_edges, interior_nodes, leg_edges


def _cluster_signal_nodes(
    nodes: gpd.GeoDataFrame,
    candidate_node_ids: set[str],
    *,
    footprint_node_ids: set[str],
) -> dict[str, str]:
    """Cluster signal nodes, then extend membership over roadway nodes in each footprint.

    ``footprint_node_ids`` limits footprint membership to nodes that carry
    roadway movements. Path, footway, and cycleway nodes that merely sit near a
    signal are not charged an entry wait.
    """
    if not candidate_node_ids:
        return {}
    available = nodes[nodes.node_id.astype(str).isin(candidate_node_ids)][
        ["node_id", "geometry"]
    ].copy()
    available["node_id"] = available.node_id.astype(str)
    available = available.drop_duplicates("node_id").set_index("node_id", drop=False)
    ids = sorted(available.node_id.astype(str))
    if not ids:
        return {}
    union = _UnionFind(ids)
    members = {node_id: {node_id} for node_id in ids}

    def union_if_compact(left: str, right: str) -> None:
        left_root = union.find(left)
        right_root = union.find(right)
        if left_root == right_root:
            return
        combined = members[left_root] | members[right_root]
        diameter = max(
            available.loc[first].geometry.distance(available.loc[second].geometry)
            for first, second in itertools.combinations(combined, 2)
        )
        if diameter > SIGNAL_CLUSTER_MAX_DIAMETER_M:
            return
        root = union.union(left_root, right_root)
        dropped = right_root if root == left_root else left_root
        members[root] = combined
        del members[dropped]

    nearby = gpd.sjoin(
        available.reset_index(drop=True),
        available.reset_index(drop=True),
        how="inner",
        predicate="dwithin",
        distance=SIGNAL_CLUSTER_DISTANCE_M,
        lsuffix="left",
        rsuffix="right",
    )
    pairs = sorted(
        {
            tuple(sorted((str(row.node_id_left), str(row.node_id_right))))
            for row in nearby.itertuples(index=False)
            if str(row.node_id_left) != str(row.node_id_right)
        },
        key=lambda pair: (
            available.loc[pair[0]].geometry.distance(available.loc[pair[1]].geometry),
            pair,
        ),
    )
    for left, right in pairs:
        union_if_compact(left, right)

    roots = sorted({union.find(node_id) for node_id in ids})
    cluster_id_by_root = {}
    for root in roots:
        digest = hashlib.sha1(
            ";".join(sorted(members[root])).encode(), usedforsecurity=False
        ).hexdigest()[:12]
        cluster_id_by_root[root] = f"signal_cluster_{digest}"

    exact = {node_id: cluster_id_by_root[union.find(node_id)] for node_id in ids}
    footprint_rows = []
    for root in roots:
        member_points = available.loc[sorted(members[root])].geometry
        footprint_rows.append(
            {
                "signal_cluster_id": cluster_id_by_root[root],
                "geometry": member_points.union_all().convex_hull.buffer(
                    SIGNAL_CLUSTER_FOOTPRINT_BUFFER_M
                ),
            }
        )
    footprints = gpd.GeoDataFrame(footprint_rows, geometry="geometry", crs=nodes.crs)
    footprint_nodes = nodes[nodes.node_id.astype(str).isin(footprint_node_ids)][
        ["node_id", "geometry"]
    ].copy()
    footprint_nodes["node_id"] = footprint_nodes.node_id.astype(str)
    nearby_nodes = gpd.sjoin(
        footprint_nodes,
        footprints,
        how="inner",
        predicate="within",
    )
    if nearby_nodes.empty:
        return exact
    centroids = footprints.set_index("signal_cluster_id").geometry.centroid
    nearby_nodes["cluster_distance"] = [
        geometry.distance(centroids.loc[str(cluster_id)])
        for geometry, cluster_id in zip(
            nearby_nodes.geometry,
            nearby_nodes.signal_cluster_id,
            strict=True,
        )
    ]
    nearby_nodes = nearby_nodes.sort_values(
        ["node_id", "cluster_distance", "signal_cluster_id"]
    ).drop_duplicates("node_id")
    membership = dict(
        zip(
            nearby_nodes.node_id.astype(str),
            nearby_nodes.signal_cluster_id.astype(str),
            strict=True,
        )
    )
    membership.update(exact)
    return membership


def build_signal_wait_inventory(
    nodes: gpd.GeoDataFrame,
    physical_edges: gpd.GeoDataFrame,
) -> SignalWaitInventory:
    """Build signal events from canonical OSM and local crossing evidence.

    Ramp meters never arrive as signals (``osm_streets.control``). Crossing legs use a single stable central
    edge, while roadway signal nodes are clustered and expanded over the
    physical intersection footprint so divided intersections are charged once.
    Only nodes on roadway edges join a footprint; nonmotor paths that pass
    near a signal are not charged.
    """
    node_ids = nodes.node_id.astype(str)
    highway = nodes.get("highway", pd.Series("", index=nodes.index)).astype(str).str.lower()
    osm_signal_ids = set(node_ids[highway.str.contains("traffic_signals")])
    wait_edges, crossing_interior_nodes, leg_edges = _crossing_events(physical_edges)
    legs = physical_edges[physical_edges.physical_edge_id.astype(str).isin(leg_edges)]
    crossing_nodes = set(legs.u.astype(str)) | set(legs.v.astype(str))
    edge_highway = (
        physical_edges.get("highway", pd.Series("", index=physical_edges.index))
        .astype(str)
        .str.lower()
    )
    edge_movement = physical_edges.get(
        "movement_type", pd.Series("along", index=physical_edges.index)
    ).astype(str)
    roadway = ~edge_highway.isin(NONMOTOR_HIGHWAYS) & edge_movement.eq("along")
    roadway_node_ids = set(physical_edges.loc[roadway, "u"].astype(str)) | set(
        physical_edges.loc[roadway, "v"].astype(str)
    )
    cluster_by_node = _cluster_signal_nodes(
        nodes,
        osm_signal_ids | crossing_interior_nodes,
        footprint_node_ids=roadway_node_ids,
    )
    arterial = roadway & edge_highway.isin(ARTERIALS)
    arterial_nodes = set(physical_edges.loc[arterial, "u"].astype(str)) | set(
        physical_edges.loc[arterial, "v"].astype(str)
    )
    return SignalWaitInventory(
        crossing_nodes=frozenset(crossing_nodes),
        arterial_clusters=frozenset(
            cluster_by_node[n] for n in arterial_nodes if n in cluster_by_node
        ),
        cluster_by_node=cluster_by_node,
        crossing_event_by_wait_edge=wait_edges,
        crossing_leg_edges=frozenset(leg_edges),
        # Every intersection a mapped signalized crossing touches, not only the
        # one holding its middle node.
        clusters_with_crossings=frozenset(
            cluster_by_node[n] for n in crossing_nodes if n in cluster_by_node
        ),
    )
