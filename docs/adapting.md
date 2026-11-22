# Adapting walkbike-routes to your town

This guide takes a new place from nothing to a map and report you can check,
then share. It assumes you have run the demo (`make demo`) and opened
`output/demo/school/map.html`.

## 1. Get a Census key

Home locations and populations come from the 2020 Census. Its API needs a
free key: sign up at <https://api.census.gov/data/key_signup.html> and set
`CENSUS_API_KEY` in your environment. Keep the key out of the repository and
out of shared logs.

OpenStreetMap, Overture building footprints, USGS elevation and HPMS traffic
counts need no key. Downloads are cached under `data/raw/<study>` (or the
folder you give with `--data`), so a second run is fast and needs no
network. The first run downloads a whole state's OpenStreetMap extract,
which can be several hundred megabytes.

## 2. Write the study config

Copy [`config/los_altos_downtown.yaml`](../config/los_altos_downtown.yaml) and
change it. First delete its `overrides:` line: those corrections name Los
Altos streets, and a correction whose streets are missing stops the run. The
fields:

- **`study`**: a name for titles.
- **`area`**: where streets and homes come from.
  - `source: place` with `place: "Boulder, Colorado"` (a Census place), or
    `source: boundary_file` with `boundary_file:` an outline of your own.
  - `buffer_m`: how far beyond the boundary to load streets and homes. Make
    it at least as far as the longest trip: a 15-minute bike ride is about
    4 km.
  - `osm_file`, `buildings`, `overture_release` and `traffic_counts` are
    optional; see "A study config" in the README, which also covers `crs`,
    `route_choice` and `screen_candidates`.
- **`destinations`**: each needs an `id` (used for folder names), a `name`
  and a `kind`.
  - A `point` has a `location: [lon, lat]`. An `area` (a downtown or a
    campus) has a `boundary_file`.
  - `access_points` are where people on foot or bike enter, such as school
    gates or the corners of a downtown grid. Without them, trips end at the
    nearest street. They matter more than anything else in the config:
    put them where people really go in.
  - `car_access_points` are drop-off curbs or car park entrances.
  - `purpose` (`everyday`, `school` or `work`) picks the distance-decay
    curves. `min_age`, `max_age` and `serves` (an attendance area: only
    homes inside it count) are optional, as is `outline`, an attendance
    area that is drawn on the map and limits the homes too (with
    `limits: false`, it is only drawn).
- **`modes`** and **`presets`**: which modes to run, and one or more
  route-choice presets for each. The first preset of a mode is its main one.
  See step 6.
- **`time_caps_min`**: the farthest walking and cycling trips go, in
  minutes. `time_bands_min` (default 5, 10, 15, 20) sets the bands in the
  tables and map; none may be above a limit.
- **`demand`**: who travels. `weight: residents` (with `min_age` and
  `max_age` for a school) or `weight: households`.

- **`exposure`** (optional): the risk points and the "fix first" rule of
  the lists of where routes meet traffic today, including an optional rule
  that makes the busiest places fix first. See "Changing the points and the
  rule" in `docs/assumptions.md`.

Unknown or missing settings stop the run with an error that names them.

## 3. Run it, small first

```bash
uv run --locked walkbike-routes run --config config/my_town.yaml --out output/my_town
```

Start with one destination, one preset per mode and a small `buffer_m`, so
the first run takes minutes rather than an hour. Each run writes:

- `output/my_town/<destination>/map.html`, the map;
- `output/my_town/report.md` and `report.html`, the tables and method note;
- the layers and per-run GeoParquet files listed in the README.

To rebuild the map and report without routing again, run
`walkbike-routes report --out output/my_town`. Add `--offline` to build
the map library into the page.

## 4. Check the map

Open each `map.html` and look for problems before reading any figures.

- **Entrances.** The yellow dots should be where people actually go in.
- **Gaps in the squares.** Walking and cycling views draw only the homes
  that can get there within the time limit. A hole in the coloured squares
  beside ones that can usually means a missing street link, a path wrongly
  closed to walking or cycling, or a gate. (The car view, which has no
  limit, draws homes that cannot get there in grey.)
- **Flow on odd streets.** Flow on a freeway ramp, a private road or through
  a building, or none on an obvious route, usually means wrong OSM tags.
  Click the street to see its name and its flow under each run.
- **Busy under only some presets** (with two or more presets). These are
  the streets whose rank depends on the route-choice model. Read them as
  uncertain.
- **The improvement screen.** Its top sections should be places where a
  sidewalk, crosswalk, signal or protected lane is plausible. Each treatment
  has its own list.

Compare with the report's "busiest streets" tables. A sidewalk there counts as
the street it runs beside, and a crossing is named for the street it crosses.

## 5. Fix what is wrong

There are two ways to fix the street data. Both are described in the README.

- **Local layers** (`local_layers`). Use these when the city or county
  publishes better sidewalk, signal, speed or bikeway data than OSM. Check
  each layer's licence. Put layers you may not publish in the config's
  private `.local.yaml` file, so every run uses them without their being
  committed. Check the report's opening lines to see which layers a run
  used.
- **Hand corrections** (`overrides`). Use these for single fixes: a missing
  path, a closed gate, a wrong tag, or a slow signal (a `signal` entry,
  with its extra wait and whether right turns on red are allowed). Each entry records
  why and how you know, and the run warns you when OSM has caught up.
  [`config/overrides/los_altos.yaml`](../config/overrides/los_altos.yaml) is
  a worked example.

Better still, fix the error in OpenStreetMap itself so everyone benefits.
The saved copy of the streets is reused until you ask for a new one: run
once with `--refresh-osm` a day or so after your edit, since the extracts
are rebuilt daily. The manifest's `data.openstreetmap.data_until` says how
recent a run's streets are.

Rerun after each change and check the map again.

## 6. Choose presets

A preset is a route-choice model: how people trade distance against busy
streets, hills, turns and crossings. They are described, with their sources,
in [`assumptions.md`](assumptions.md), and the report's method note quotes
each one used.

- `bike/broach-2011`, `walk/broach-2016`: estimated from Portland adults'
  GPS traces. The suggested first presets. Of the published cycling
  presets, `bike/broach-2011` routes were the closest to recorded trips in
  Philadelphia ([`validation/RESULTS.md`](../validation/RESULTS.md)); the
  walking one has only a weak, preliminary check against counts.
- `walk/broach-2016-sidewalks`: the same walking model, plus a sidewalk
  term from a Toronto study: collectors and major roads without sidewalk
  evidence count as 1.5 times their length. Local streets are left alone,
  a judgment for suburbs where most residential streets have no sidewalk.
  Worth running beside `broach-2016`, and the one to list first if you want
  an adult sidewalk screen.
- `bike/broach-2011-child`, `walk/broach-2016-child`: the same models with
  a child's speed, twice the penalties for busy streets and unsignalised
  crossings, and (walking) a penalty for missing sidewalks. Assumptions:
  no route-choice model for children was found. The suggested first
  presets for an elementary school, with the adult ones listed after them
  for comparison.
- `bike/broach-2011-lanes`: the same, but a bike lane or track counts as
  20% shorter. It fitted Philadelphia's trips best, but that lane bonus is
  this tool's assumption, chosen after seeing those trips. Worth running
  beside `broach-2011` where most lanes are on quiet streets.
- `legacy`, `legacy-hills`: hand-set planning judgments, not estimated from
  data. `bike/legacy` was the furthest of all from Philadelphia riders'
  routes.
- `bike/furth`: traffic stress by Furth's criteria tables. In Philadelphia
  it kept to quiet streets far more than riders did.
- `bike/shortest`, `walk/shortest`: plain shortest distance, a
  baseline.
- `child`: the same idea built on the legacy presets. No preset has been
  checked against children's routes.

The first preset listed for a mode is its main one: the map shows it first
and the improvement screen uses it. `walk/broach-2016` has no sidewalk
term, so with it first there is no sidewalk screen (crosswalks and signals
are still screened); list `walk/broach-2016-sidewalks` first to get one. Run at least two presets
per mode.
Where they agree, the result does not depend much on the model; where they
disagree, say so. No preset has been checked against counts or trips in
your town.

## 7. Before you share

- **Flows are not counts.** They show where trips would go if people made
  them, weighted by national distance-decay shares; they are not how many
  people walk or ride. The map and report say so; keep that wording when you
  quote them.
- **Homes.** Homes are placed in Census blocks' buildings. The map shows only
  150 m squares, but the output folder's `homes.parquet` and `routes.parquet`
  have every home point. Do not publish those files.
- **Local data.** A map made with a local layer or a hand correction includes
  it. Check that its licence allows sharing, and that it contains nothing
  private.
- **Credits.** Keep the credits that the map and report print. OpenStreetMap
  and Overture data are under the ODbL, which requires the credit.
- **The improvement screen** is a list of where to look, not a design or a
  recommendation. The overlap layer is where to look, not a measure of risk.
- **The demo town** is made up. Never present its figures as a real place.
