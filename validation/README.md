# Validation (experimental)

Checks of walkbike-routes against observed walking and cycling. This folder
is kept apart from the package while it is an experiment: it imports
`walkbike_routes` as a library, adds no commands or modules to it, and can be
deleted without touching anything else. `make check` does not cover it; run
`make validation-check` (lint, types and its own tests).

```
validation/
  run.py           the command line (python validation/run.py --help)
  wbvalidate/      the checks
  config/          the central Philadelphia study used for both checks
  tests/           tests on made-up data
```

Results of the Philadelphia run are in [RESULTS.md](RESULTS.md).

## The two checks

### Routes against recorded trips (`routes`)

```
uv run --locked python validation/run.py routes \
    --config validation/config/philadelphia_core.yaml --out <run> --traces cyclephilly
```

Each recorded bicycle trip is routed from its first point to its last under
each of the study's cycling presets, and the modelled route is compared with
the route ridden. Demand plays no part, so this tests route choice alone.

- **Trips used.** Trips wholly inside the study boundary (or `--within`),
  0.5 to 15 km long, not recorded as exercise, not loops (ends at least
  300 m and 0.4 × the distance ridden apart), and in one piece (gaps under
  50 m). These filters look only at the trace, so they favour no preset.
- **Ends.** The trace's first and last points are attached to the network
  as homes are.
- **Overlap.** The mean of two shares: of the trace within 20 m of the
  modelled route, and of the modelled route within 20 m of the trace. It is
  1 when the routes are the same. 10 m and 30 m are shown as a sensitivity.
- **Scores.** Over the trips every preset routed: mean overlap, the share of
  trips with overlap of 0.8 or more, and the difference from
  `shortest`. Intervals are a bootstrap over riders (each drawn with
  all their trips), since a few riders made many trips.
- **Makeup.** In a grid, many routes tie on length, so a preset can be right
  about what riders want and still pick a different one of the tied
  streets. For each route, ridden or modelled, the report sums the share on
  separated paths and tracks, on bike lanes, on low-stress (LTS 1–2) and
  high-stress (LTS 3–4) streets and against a one-way, and the turns and
  busy-street crossings per km, then compares each preset with the ridden
  routes.
- **Trips per street.** Each link's count of ridden trips against its count
  of modelled trips, by Spearman's rank correlation over the links any route
  used, and the named streets where presets miss riders most. Summing many
  trips cancels the arbitrary choice between tied routes.

Ridden and modelled routes are matched to links the same way (a link is used
when 60% of it lies within 20 m of the route), so matching errors fall on
both.

It needs only a run's `links.parquet` and `nodes.parquet` (a run stopped
early will do), and writes `<run>/validation/routes/`: `report.md`,
`report.html`, `trips.parquet` (the trip records) and `streets.parquet`
(trips per link, which at low counts can trace one rider's routes). Keep
both parquet files private.

A traces file is any GeoJSON or GeoParquet of lines with the columns
`trip_id`, `user_id`, `purpose` and `group`; `cyclephilly` downloads
Philadelphia's.

### Flows against counts (`counts`)

```
uv run --locked python validation/run.py counts --out <run> --counts dvrpc \
    --within validation/config/philadelphia_core.geojson
```

This compares a `network_flows` run with walking and cycling counts. A
counts file is any GeoJSON or GeoParquet of points with the columns
`site_id`, `mode` (`walk` or `bike`), `volume` (per day), `year`, `road`,
`direction` (`both`, or the one direction counted), `side`, `facility` and
`source`. Each site is matched to the street of that name within 25 m, and
each run is scored by how well it ranks the sites (Spearman's ρ with a 95%
bootstrap interval), next to the shortest-distance run and a score from
street class alone. Sites that cannot be compared are listed with the
reason.

It writes `<run>/validation/report.md`, `report.html` and `sites.parquet`
(the count records: keep it private). Network flows model only home-to-job
trips, so this mixes route choice with those demand assumptions; the route
check above is the cleaner test.

Nothing is fitted to the trips or the counts in either check.

## The Philadelphia study

`config/philadelphia_core.yaml` is a 6 by 6 km rectangle over Center City,
University City and South Philadelphia with a 3 km buffer. Run it with the
package as usual:

```
uv run --locked walkbike-routes run --config validation/config/philadelphia_core.yaml \
    --out output/philadelphia_core --no-map
```

The full run (network flows from 40 job clusters under every preset) takes
hours; the route check needs only the streets it writes first.

## Data and credits

None of these records are committed, and only aggregate figures computed
from them may be published, with the credits below.

- Recorded bicycle trips: CyclePhilly, a Code for Philly app, published by
  the Delaware Valley Regional Planning Commission
  (<https://www.dvrpc.org/webmaps/cyclephilly/>): about 12,000 trips by about
  300 riders, May 2014 to April 2016, snapped by DVRPC to its 2010
  OpenStreetMap streets, with trip ends blurred. DVRPC's notes say these
  riders may not ride like cyclists in general. The download carries a
  disclaimer but no named licence; credit Code for Philly and DVRPC.
- Counts: Delaware Valley Regional Planning Commission, under the
  [DVRPC Data License](https://catalog.dvrpc.org/dvrpc_data_license.html),
  which asks that DVRPC be credited wherever the data are used.
