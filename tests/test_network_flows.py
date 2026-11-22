"""Flows across the whole network to job clusters (walkbike_routes.network_flows)."""

from __future__ import annotations

import gzip
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pytest
import yaml
from shapely.geometry import LineString, Point

from walkbike_routes import __main__ as cli
from walkbike_routes import config, demo, layers, network_flows
from walkbike_routes import study as study_runs
from walkbike_routes.adapters import canonical_origins
from walkbike_routes.costs import presets
from walkbike_routes.demand.jobs import cluster_jobs, gravity_trips
from walkbike_routes.download import census, lodes
from walkbike_routes.download.http import DownloadError
from walkbike_routes.exceptions import AccessibilityError
from walkbike_routes.network import prepare

CRS = "EPSG:32613"


def _config(tmp_path: Path, **changes: Any) -> Path:
    raw: dict[str, Any] = {
        "study": "Demo town network",
        "synthetic": True,
        "area": {"source": "demo"},
        "destinations": [],
        "modes": ["walk"],
        "presets": {"walk": ["legacy"]},
        "time_caps_min": {"walk": 20},
        "demand": {"weight": "residents"},
        "network_flows": {"destinations": 3},
    }
    raw.update(changes)
    raw = {key: value for key, value in raw.items() if value is not None}  # None: left out
    path = tmp_path / "study.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


def _points(rows: list[tuple[str, int, float, float]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"block_id": [r[0] for r in rows], "jobs": [r[1] for r in rows]},
        geometry=[Point(r[2], r[3]) for r in rows],
        crs=CRS,
    )


def test_gravity_shares_each_home_by_jobs_times_decay() -> None:
    access = pd.DataFrame(
        {
            "origin_id": ["h1", "h1", "h2", "h3", "h3"],
            "destination_id": ["a", "b", "a", "a", "b"],
            "people": [10.0, 10.0, 4.0, 6.0, 6.0],
            "attraction": [100.0, 50.0, 100.0, 100.0, 50.0],
            "decay_factor": [0.5, 1.0, 0.0, 0.2, 0.2],
        }
    )
    trips = gravity_trips(access).set_index(["origin_id", "destination_id"]).trips
    # h1: pulls 100 x 0.5 = 50 and 50 x 1 = 50, so half each.
    assert trips["h1", "a"] == pytest.approx(5.0) and trips["h1", "b"] == pytest.approx(5.0)
    assert trips["h2", "a"] == 0.0  # no pull anywhere: no trips
    # h3: equal decay, so shared by jobs alone, 2 to 1.
    assert trips["h3", "a"] == pytest.approx(4.0) and trips["h3", "b"] == pytest.approx(2.0)
    bad = access.assign(decay_factor=[0.5, -1.0, 0.0, 0.2, 0.2])
    with pytest.raises(AccessibilityError, match="decay_factor"):
        gravity_trips(bad)


def test_clusters_follow_the_jobs_and_are_repeatable() -> None:
    jobs = _points(
        [("1", 100, 0, 0), ("2", 300, 10, 0), ("3", 50, 5000, 5000), ("4", 50, 5010, 5000)]
    )
    clusters = cluster_jobs(jobs, 2)
    assert clusters.destination_id.tolist() == ["jobs_001", "jobs_002"]
    assert clusters.jobs.tolist() == [400, 100] and clusters.blocks.tolist() == [2, 2]
    # Placed on the member nearest the job-weighted centre (7.5, 0): block 2.
    assert clusters.geometry.iloc[0].equals(Point(10, 0))
    assert clusters.name.iloc[0] == "Jobs cluster 1 (400 jobs)"
    again = cluster_jobs(jobs.sample(frac=1, random_state=3), 2)
    assert again.to_wkt().equals(clusters.to_wkt())
    every = cluster_jobs(jobs, 10)
    assert len(every) == 4 and every.jobs.sum() == 500
    with pytest.raises(AccessibilityError, match="projected"):
        cluster_jobs(jobs.to_crs(4326), 2)
    with pytest.raises(AccessibilityError, match="no jobs"):
        cluster_jobs(jobs.assign(jobs=0), 2)


def test_a_cluster_is_placed_only_where_allowed_when_it_can_be() -> None:
    jobs = _points([("1", 100, 0, 0), ("2", 300, 10, 0), ("3", 50, 30, 0)])
    # The centre (11.7, 0) is nearest block 2, but only blocks 1 and 3 are near streets.
    near = pd.Series([True, False, True], index=jobs.index)
    placed = cluster_jobs(jobs, 1, placeable=near)
    assert placed.geometry.iloc[0].equals(Point(0, 0)) and placed.jobs.iloc[0] == 450
    nowhere = cluster_jobs(jobs, 1, placeable=pd.Series(False, index=jobs.index))
    assert nowhere.geometry.iloc[0].equals(Point(10, 0))


def test_a_cluster_centred_away_from_streets_moves_to_a_member_near_one(
    tmp_path: Path,
) -> None:
    study = config.load(_config(tmp_path, network_flows={"destinations": 1}))
    town = demo.build()
    x0, y0 = demo.ORIGIN
    # A heavy block far from every street, beside a light one on a street corner.
    pair = _points([("yard", 5000, x0 - 1500, y0), ("gate", 10, x0 - 20, y0)])
    pair = pair.set_crs(demo.CRS, allow_override=True)
    jobs = gpd.GeoDataFrame(pair, geometry="geometry", crs=demo.CRS)
    network_flows.analyze(study, replace(town, jobs=jobs), tmp_path)
    summary = pd.read_csv(tmp_path / "network" / "summary.csv").iloc[0]
    assert summary.destinations_too_far == 0 and summary.destinations_reached == 1
    placed = gpd.read_parquet(tmp_path / "network" / "destinations.parquet")
    assert placed.geometry.iloc[0].equals(Point(x0 - 20, y0))


def test_placement_uses_nodes_walkers_or_cyclists_can_reach() -> None:

    nodes = gpd.GeoDataFrame(
        {"node_id": ["a", "b"]}, geometry=[Point(0, 0), Point(1000, 0)], crs=CRS
    )
    town = demo.build()
    street = town.links.iloc[[0]].to_crs(CRS)
    street = street.assign(u="a", v="b", highway="residential", link_id="s")
    street = street.set_geometry([LineString([(0, 0), (1000, 0)])], crs=CRS)
    town = replace(town, nodes=nodes, links=street)
    # On the street but 500 m from either end, where routing snaps.
    jobs = _points([("end", 1, 0, 5), ("middle", 100, 500, 0)])
    assert network_flows._near_streets(jobs, town).tolist() == [True, False]
    placed = cluster_jobs(jobs, 1, placeable=network_flows._near_streets(jobs, town))
    assert placed.geometry.iloc[0].equals(Point(0, 5))
    motorway = replace(town, links=town.links.assign(highway="motorway"))
    assert not network_flows._near_streets(jobs, motorway).any()


def test_destinations_may_be_empty_only_with_network_flows(tmp_path: Path) -> None:
    assert config.load(_config(tmp_path)).network_flows == config.NetworkFlows(destinations=3)
    with pytest.raises(config.ConfigError, match="or add network_flows"):
        config.load(_config(tmp_path, network_flows=None))
    # Every setting under it commented out is not a quiet way to turn it off.
    emptied = _config(tmp_path, network_flows=None)
    emptied.write_text(emptied.read_text() + "network_flows:\n")
    with pytest.raises(config.ConfigError, match="network_flows: is empty"):
        config.load(emptied)
    with pytest.raises(config.ConfigError, match="cars are not included"):
        config.load(_config(tmp_path, modes=["car"], presets={"car": ["central"]}))
    with pytest.raises(config.ConfigError, match="jobs are not schools"):
        config.load(_config(tmp_path, network_flows={"purpose": "school"}))
    with pytest.raises(config.ConfigError, match="1 or more"):
        config.load(_config(tmp_path, network_flows={"destinations": 0}))
    with pytest.raises(config.ConfigError, match="unknown setting"):
        config.load(_config(tmp_path, network_flows={"clusters": 5}))


@pytest.fixture(scope="module")
def network_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, config.Study]:
    folder = tmp_path_factory.mktemp("network")
    study = config.load(_config(folder, distance_decay={"walk": "none"}))
    out = folder / "out"
    cli.build(study, out)
    return out, study


def test_flows_add_each_clusters_routes_at_its_gravity_share(
    network_run: tuple[Path, config.Study], tmp_path: Path
) -> None:
    """Route each cluster on its own and redo the gravity sum by hand."""
    out, study = network_run
    town = demo.build()
    clusters = gpd.read_parquet(out / "network" / "destinations.parquet")
    homes = town.homes.rename(columns={"home_id": "origin_id"})
    context = study_runs.Context(
        study=study,
        edges=prepare.physical_edges(town.links, town.nodes),
        nodes=prepare.nodes(town.nodes),
        origins=canonical_origins(homes, weight_field="residents", weight_unit="residents"),
        destinations=clusters,
        gates=town.access_points.iloc[:0],
        out=tmp_path,
    )
    preset = presets.load("walk", "legacy")
    shares = []
    for row in clusters.itertuples(index=False):
        destination = config.Destination(id=str(row.destination_id), name="x", kind="point")
        folder = tmp_path / destination.id
        folder.mkdir()
        study_runs.active_run(context, destination, "walk", preset, folder, screen=False)
        part = pd.read_parquet(folder / "route_shares.parquet")
        shares.append(part.assign(jobs=float(str(row.jobs))))
    every = pd.concat(shares, ignore_index=True)
    # Decay is off, so each home's people go to the clusters it reaches in proportion to jobs.
    reached = every.drop_duplicates(["origin_id", "jobs"]).groupby("origin_id").jobs.sum()
    every["weight"] = every.assigned_weight * every.jobs / every.origin_id.map(reached)
    expected: dict[str, float] = {}
    for edges, weight in zip(every.physical_edge_ids, every.weight, strict=True):
        for edge in set(str(edges).split(";")) - {""}:
            expected[edge] = expected.get(edge, 0.0) + weight
    flows = gpd.read_parquet(out / "network" / "walk-legacy" / "flows.parquet")
    found = flows.set_index("physical_edge_id").potential_flow
    assert found[found.gt(0)].to_dict() == pytest.approx(expected)
    summary = pd.read_csv(out / "network" / "summary.csv").iloc[0]
    assert summary.assigned_weight == pytest.approx(
        context.origins[context.origins.origin_id.isin(reached.index)].demand_weight.sum()
    )
    assert summary.decay_curve == "none" and summary.destinations == 3


def test_a_network_only_run_writes_no_maps_and_lists_its_files(
    network_run: tuple[Path, config.Study],
) -> None:
    out, _ = network_run
    manifest = json.loads((out / "manifest.json").read_text())
    for name in ("jobs.parquet", "network/destinations.parquet", "network/summary.csv",
                 "network/walk-legacy/flows.parquet"):  # fmt: skip
        assert name in manifest["files"], name
    assert not (out / "network" / "walk-legacy" / "by_destination").exists()
    assert not (out / "report.md").exists()
    cli.main(["report", "--out", str(out)])  # nothing to draw, and no error
    assert not (out / "report.md").exists()


def test_workers_give_the_same_flows(tmp_path: Path) -> None:
    def flows(workers: int) -> pd.Series:
        flows_settings = {"destinations": 3, "workers": workers}
        study = config.load(_config(tmp_path, network_flows=flows_settings))
        out = tmp_path / f"w{workers}"
        cli.build(study, out)
        frame = gpd.read_parquet(out / "network" / "walk-legacy" / "flows.parquet")
        return frame.set_index("physical_edge_id").potential_flow

    pd.testing.assert_series_equal(flows(1), flows(2))


def test_the_study_destinations_still_run_beside_the_network(tmp_path: Path) -> None:
    path = _config(
        tmp_path, destinations=[{"id": "school", "name": "Demo Elementary", "kind": "point"}]
    )
    out = tmp_path / "out"
    cli.main(["run", "--config", str(path), "--out", str(out), "--no-map"])
    assert (out / "school" / "walk-legacy" / "flows.parquet").exists()
    assert (out / "network" / "walk-legacy" / "flows.parquet").exists()
    assert (out / "report.md").exists()
    assert not pd.read_csv(out / "summary.csv").destination_id.eq("network").any()


def test_a_study_without_jobs_is_refused(tmp_path: Path) -> None:
    study = config.load(_config(tmp_path))
    with pytest.raises(config.ConfigError, match="no jobs"):
        network_flows.analyze(study, demo.build(), tmp_path)


class _Files:
    """A fetcher stand-in whose downloads are files written by the test."""

    def __init__(self, folder: Path, files: dict[str, str]) -> None:
        self.folder, self.files = folder, files
        self.asked: list[str] = []

    def download(self, url: str, name: str, **_: Any) -> Path:
        self.asked.append(url)
        path = self.folder / name
        with gzip.open(path, "wt") as out:
            out.write(self.files[url.rsplit("/", 1)[1]])
        return path


def test_lodes_jobs_are_summed_by_block_across_states(tmp_path: Path) -> None:
    fetcher = _Files(
        tmp_path,
        {
            "pa_wac_S000_JT00_2023.csv.gz": "w_geocode,C000,CA01\n421010001001000,12,3\n"
            "421010001001001,0,0\n",
            "nj_wac_S000_JT00_2023.csv.gz": "w_geocode,C000,CA01\n340070001001000,5,1\n",
        },
    )
    jobs = lodes.workplace_jobs(["42", "34", "42"], fetcher)  # type: ignore[arg-type]
    assert jobs.to_dict("list") == {
        "block_id": ["340070001001000", "421010001001000", "421010001001001"],
        "jobs": [5, 12, 0],
    }
    assert fetcher.asked == [
        f"{lodes.BASE}/nj/wac/nj_wac_S000_JT00_2023.csv.gz",
        f"{lodes.BASE}/pa/wac/pa_wac_S000_JT00_2023.csv.gz",
    ]
    with pytest.raises(DownloadError, match="FIPS"):
        lodes.workplace_jobs(["99"], fetcher)  # type: ignore[arg-type]


def test_a_real_run_with_jobs_credits_lodes() -> None:
    study = config.load(Path(__file__).resolve().parents[1] / "config" / "demo.yaml")
    real = replace(study, area=replace(study.area, source="place"), synthetic=False)
    town = demo.build()
    assert lodes.CREDIT not in cli.sources(real, town)
    assert lodes.CREDIT in cli.sources(real, replace(town, jobs=demo.jobs()))


def test_a_real_study_may_have_no_destinations_of_its_own() -> None:
    study = config.parse(
        {
            "study": "x", "area": {"source": "place", "place": "Made-up, Nowhere"},
            "destinations": [], "modes": ["walk"], "presets": {"walk": ["legacy"]},
            "time_caps_min": {"walk": 20}, "demand": {"weight": "residents"},
            "network_flows": {},
        }
    )  # fmt: skip
    destinations, access = layers._destinations(study, CRS)
    assert destinations.empty and access.empty
    assert {"destination_id", "geometry"} <= set(destinations.columns)


def test_network_is_not_a_destination_id(tmp_path: Path) -> None:
    named = [{"id": "network", "name": "A place", "kind": "point"}]
    for flows in ({}, {"network_flows": None}):  # with network flows or without
        with pytest.raises(config.ConfigError, match="folder the run writes itself"):
            config.load(_config(tmp_path, destinations=named, **flows))


def test_a_cluster_far_from_the_streets_is_left_out_not_fatal(tmp_path: Path) -> None:
    study = config.load(_config(tmp_path))
    far = _points([("far", 5000, 0.0, 0.0)]).set_crs(demo.CRS, allow_override=True)
    jobs = pd.concat([demo.jobs(), far], ignore_index=True)
    town = replace(demo.build(), jobs=gpd.GeoDataFrame(jobs, geometry="geometry", crs=demo.CRS))
    network_flows.analyze(study, town, tmp_path)
    summary = pd.read_csv(tmp_path / "network" / "summary.csv").iloc[0]
    assert summary.destinations_too_far == 1 and summary.jobs_too_far == 5000
    flows = gpd.read_parquet(tmp_path / "network" / "walk-legacy" / "flows.parquet")
    assert flows.potential_flow.gt(0).any()


def test_a_job_block_reaching_past_the_area_keeps_its_jobs_inside(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from shapely.geometry import box

    block = gpd.GeoDataFrame(
        {"block_id": ["421010001001000"], "STATE": ["42"]},
        geometry=[box(0, 0, 1000, 1000)],
        crs=CRS,
    ).to_crs(4326)
    monkeypatch.setattr(census, "blocks", lambda bbox, fetcher: block)
    monkeypatch.setattr(
        lodes,
        "workplace_jobs",
        lambda states, fetcher, year: pd.DataFrame(
            {"block_id": ["421010001001000"], "jobs": [100]}
        ),
    )
    extent = gpd.GeoDataFrame(geometry=[box(0, 0, 400, 1000)], crs=CRS)
    place = SimpleNamespace(crs=CRS, extent=extent)
    jobs = layers._jobs((0, 0, 1, 1), place, None, 2023)  # type: ignore[arg-type]
    assert jobs.jobs.tolist() == [100]
    assert jobs.geometry.iloc[0].within(box(-1e-6, -1e-6, 400 + 1e-6, 1000 + 1e-6))
