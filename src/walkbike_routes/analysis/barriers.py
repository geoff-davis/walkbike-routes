"""Generic barrier-section grouping for intervention discovery."""

from __future__ import annotations

import itertools
import math
from collections import defaultdict
from collections.abc import Callable, Mapping
from typing import Any

import networkx as nx
import pandas as pd

from walkbike_routes.analysis.places import UnionFind, section_label

BarrierTest = Callable[[Mapping[str, Any]], bool]
NameLookup = Callable[[str, Mapping[str, Any]], str]


def clean_text(value: object) -> str:
    """Return a clean string for a possibly missing attribute."""
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    result = str(value).strip()
    return "" if result.lower() in {"nan", "none", "<na>"} else result


def link_length(attrs: Mapping[str, Any]) -> float:
    """Return physical link length, preferring an explicit physical field."""
    value = attrs.get("physical_length_m")
    return float(value if value is not None else attrs["length_m"])


def barrier_sections(
    graph: nx.Graph[str],
    is_barrier: BarrierTest,
    *,
    physical_id: Callable[[str], str] | None = None,
    name_of: NameLookup = lambda _pid, attrs: clean_text(attrs.get("name")),
) -> tuple[dict[str, str], pd.DataFrame]:
    """Group barrier links into same-street runs between comfortable connections.

    A section is a maximal run of barrier links that share a street name and
    meet only at nodes with no comfortable link. Any node touched by a
    comfortable link splits the run. Returns a map from raw link id to section
    id and a section table.

    A link's raw id is its ``routable_link_id`` (``edge_id`` in older graphs).
    Links are grouped by ``physical_id(raw_id)`` when given, otherwise by
    their ``physical_edge_id``, so both directions of a street count once.
    """
    open_nodes: set[str] = set()
    open_names: defaultdict[str, set[str]] = defaultdict(set)
    links: dict[str, dict[str, Any]] = {}
    for u, v, attrs in graph.edges(data=True):
        raw_id = clean_text(attrs.get("routable_link_id")) or clean_text(attrs.get("edge_id"))
        if physical_id is not None:
            pid = physical_id(raw_id)
        else:
            # Barrier crossing links have no physical edge; they stand alone.
            pid = clean_text(attrs.get("physical_edge_id")) or raw_id
        name = name_of(pid, attrs)
        if not is_barrier(attrs):
            open_nodes.update((str(u), str(v)))
            if name:
                open_names[str(u)].add(name)
                open_names[str(v)].add(name)
            continue
        record = links.setdefault(
            pid,
            {"nodes": set(), "raw_ids": set(), "name": name, "length": link_length(attrs)},
        )
        record["nodes"].update((str(u), str(v)))
        record["raw_ids"].add(raw_id)
    by_node: defaultdict[str, list[str]] = defaultdict(list)
    for pid, record in links.items():
        for node in record["nodes"]:
            by_node[node].append(pid)
    union = UnionFind()
    for pid in links:
        union.add(pid)
    for node, pids in by_node.items():
        if node in open_nodes:
            continue
        by_name: defaultdict[str, list[str]] = defaultdict(list)
        for pid in pids:
            by_name[links[pid]["name"]].append(pid)
        for group in by_name.values():
            for left, right in itertools.pairwise(sorted(group)):
                union.union(left, right)
    members: defaultdict[str, list[str]] = defaultdict(list)
    for pid in links:
        members[union.find(pid)].append(pid)
    ordered = sorted(members.values(), key=lambda pids: (links[pids[0]]["name"], min(pids)))
    section_by_edge: dict[str, str] = {}
    rows: list[dict[str, object]] = []
    for index, pids in enumerate(ordered, start=1):
        section_id = f"S{index:04d}"
        name = links[pids[0]]["name"]
        nodes: defaultdict[str, int] = defaultdict(int)
        for pid in pids:
            for node in links[pid]["nodes"]:
                nodes[node] += 1
            for raw_id in links[pid]["raw_ids"]:
                section_by_edge[raw_id] = section_id
        endpoints = sorted(
            node for node, count in nodes.items() if node in open_nodes or count == 1
        )
        cross_streets: list[str] = []
        for node in endpoints:
            for other in sorted(open_names.get(node, ())):
                if other.startswith("unnamed"):
                    continue  # "unnamed footway" tells nobody where the section ends
                if other != name and other not in cross_streets:
                    cross_streets.append(other)
                    break
        rows.append(
            {
                "section_id": section_id,
                "name": name,
                "label": section_label(name, cross_streets),
                "physical_edge_ids": ";".join(sorted(pids)),
                "edge_count": len(pids),
                "length_m": float(sum(links[pid]["length"] for pid in pids)),
                "endpoint_nodes": ";".join(endpoints),
                "cross_streets": ";".join(cross_streets),
            }
        )
    columns = [
        "section_id",
        "name",
        "label",
        "physical_edge_ids",
        "edge_count",
        "length_m",
        "endpoint_nodes",
        "cross_streets",
    ]
    return section_by_edge, pd.DataFrame(rows, columns=columns)
