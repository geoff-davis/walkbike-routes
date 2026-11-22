# Routing assumptions and their sources

Every number the routing rests on, where it comes from, and how sure it is.
"Hand-set" means a planning judgment, not estimated from data. Unless a
section says otherwise, values are the same for every study, and you change
them through presets (`src/walkbike_routes/presets/`) or the study config.

## What a run counts

- **Walking and cycling.** Each home gets its lowest-cost route under the
  preset, and counts only if that route's travel time fits the mode's time
  cap. A home whose best route runs over the cap is left out, even if a
  costlier route would fit. The home's residents (or households) are then multiplied by a
  distance-decay share at the route's length, described below, and the result
  is spread over near-best routes. Flows are the sum of those weights on each
  street. With `distance_decay: none`, every resident within the cap counts in
  full: the cap becomes a hard limit.
- **Cars.** Every resident counts in full, with no cap unless one is set,
  for the trip there and the trip back.
- **What it is not.** Neither figure is a count or a forecast of trips. The
  catchment columns of `summary.csv` (`within_cap_weight`,
  `within_N_min_weight`) are never decayed: they count the study's demand
  weight (residents, an age group or households) in full. `expected_weight`
  is the decayed total. `median_minutes_within_cap` is the median trip of
  the homes within the time limit (the median the map and report show);
  `median_minutes` is the median of every home that can get there at all,
  however long the trip.

## Distance decay (walking and cycling)

**Source.** The 2017 National Household Travel Survey (NHTS), version 1.2,
public trip file, from the Federal Highway Administration
(<https://nhts.ornl.gov>).

**What a curve is.** The share of a mode's trips for a purpose that are at
least a given distance long, using the survey's trip weights. Distance is the
survey's `TRPMILES`, which the survey derives from the Google Maps route
between the trip ends (NHTS 2017 Derived Variables, v1.2). Loop trips, whose
distance was reported rather than routed, are left out.

**How it is applied.** A curve is read with straight lines between its points.
The length used is the home's route plus its link to the network. Each curve
file is in `src/walkbike_routes/decay/`, and `scripts/nhts_decay_curves.py`
rebuilds them all from the survey file.

| Curve | Trips used | Trips sampled | Median length |
|---|---|---:|---:|
| `walk-everyday` | shopping, services, meals, errands, recreation and visits, ages 16+ | 19,911 | 0.27 mi |
| `bike-everyday` | the same purposes and ages | 1,589 | 1.08 mi |
| `walk-work` | to work | 5,213 | 0.30 mi |
| `bike-work` | to work | 828 | 1.76 mi |
| `walk-school-5-10` | to school, ages 5–10 | 600 | 0.39 mi |
| `walk-school-11-13` | to school, ages 11–13 | 263 | 0.47 mi |
| `walk-school-14-17` | to school, ages 14–17 | 361 | 0.56 mi |
| `walk-school-5-17` | to school, ages 5–17 | 1,224 | 0.47 mi |
| `bike-school-5-17` | to school, ages 5–17 | 223 | 0.95 mi |

**Which curve a destination gets.** It follows the destination's `purpose`:
`everyday` (the default), `school` or `work`.

- **Schools.** The middle of the age range picks the walking curve: under 11,
  11 to 13, or 14 and over. With no ages, the 5–17 curve is used. Cycling to
  school uses the 5–17 curve whatever the ages, because the survey has only 56
  to 95 school bike trips in each age group.
- **Overrides.** The study's `distance_decay` can name a curve for each mode.
  `none` turns decay off.

**Limits.**

- **National, not local.** The curves describe trips people already make,
  across the whole country. In places where walking or cycling is easy,
  people go farther.
- **Opportunity.** Trip lengths also reflect how far away the nearest shop or
  school is. The curve is not a pure measure of willingness to travel.
- **Bike sample.** Cycling samples are small, so the curves are coarse past
  about 2 miles.
- **No mode choice.** A curve says nothing about whether anyone makes the
  trip, or by which mode. National shares of trips by bike are only 1–5% at
  any distance (same survey), and they are not applied.
- **Improvement screens.** Screens weight each home at its present route
  length, not at the shorter length after a treatment.

The time cap still applies, as the farthest distance counted.

## Street network (OpenStreetMap)

- **Coverage.** Ways with a `highway` tag in the usual road and path classes
  are used. They are split at every junction, signal and stop sign.
- **Who may use a way.**
  - **Walking and cycling:** never on motorways or trunk roads, and cyclists
    not on footways, steps or pedestrian ways. A `foot` or `bicycle` tag (or
    `vehicle` for bicycles) overrides these road-class rules; a general
    `access` tag only closes ways the road class leaves open.
  - **Cars:** not on paths, footways, cycleways, steps, tracks or bridleways.
    `motorcar`, `motor_vehicle` or `vehicle` overrides this; `access` only
    closes ways.
- **One-way rules.** Cycling and driving follow `oneway`; walking goes both
  ways on every link. Roundabouts and motorways are one
  way. Cyclists may ride against the flow where `oneway:bicycle=no` or a
  contraflow lane or track is mapped.
- **Speeds.** A bare `maxspeed` is read as km/h, as OSM defines it. `mph` and
  `knots` are converted.
- **Bike facilities.** From `cycleway*` tags: a track outranks a lane, which
  outranks nothing. A cycleway, or a path or footway that allows bicycles,
  counts as a path. A street tagged `bicycle_road=yes` or `cyclestreet=yes`
  is a bike boulevard.
- **Sidewalks.** From `sidewalk*` tags. Footways and paths count as walkways
  themselves. An untagged street has unknown sidewalks.
- **Crosswalks.** A crossing way (`footway=crossing` and the like) is a
  signalised crossing link when it is tagged `crossing=traffic_signals` or
  `crossing:signals=yes`, or passes through a signal node and is not tagged
  `crossing=no`, `crossing=uncontrolled` or `crossing:signals=no`. A node
  tagged `crossing:signals=yes` counts as a signal. The crossing records the
  busiest road class it meets as the road it crosses. Other crossing ways,
  including signalised ones mapped with no tag saying so, stay ordinary
  footways.
- **Hills.** Node elevations come from USGS 3DEP (1 m lidar where it
  exists, 10 m elsewhere). A link's grade is its rise between its end nodes
  over its length. Bridges and tunnels, where the ground model is not the
  way's height, links meeting their ends (the bare-earth model can put a
  bridge's end below its deck), and links shorter than 10 m, where small elevation errors
  make steep false grades, have no grade. Grades are capped at 30%. A link
  over a crest counts only its net rise. The `legacy-hills`, `child` and
  `bike/furth` presets use grade only for speed. The Broach presets add
  their models' climbing terms to route choice; travel time ignores grade
  in the cycling ones and slows on hills in `walk/broach-2016`.

## Walking (`walk/legacy`, `walk/legacy-hills`, `walk/shortest`)

Hand-set for a Los Altos downtown access study, and not estimated from
observed route choice.

- **Speed: 3.0 mph** on the flat. Hand-set.
- **Hills** (`walk/legacy-hills` only; otherwise the same as `walk/legacy`).
  Speed is multiplied by Tobler's hiking function as a ratio to its flat
  value, exp(−3.5 (|g + 0.05| − 0.05)) for grade g (Tobler, W. 1993, "Three
  presentations on geographical analysis and modeling", National Center for
  Geographic Information and Analysis Technical Report 93-1, fitted to
  Eduard Imhof's Swiss data): fastest, × 1.19, on a 5% descent; × 0.70 at
  10% up or 20% down. Wood, Mackaness, Simpson and Armstrong (2023,
  "Improved prediction of hiking speeds using a data driven approach",
  arXiv:2303.16065), testing it against about 88,000 km of UK walking and
  hiking GPS tracks, found its error going straight up or down slopes
  similar to their own model's, but that it overstates speed on slight
  descents, where its peak is. Not checked against city walking.
- **Signal waits.** The wait depends on the road crossed. A main road is a
  primary, trunk or secondary road; signals there usually give its through
  traffic the long green and the side street a short phase.
  - **Across a main road: 50 s.**
  - **Across a side street at a main-road signal: 15 s.**
  - **At a signal with no main road: 30 s**, hand-set by the old study.

  The first two use the average pedestrian delay from the Highway Capacity
  Manual (Transportation Research Board, 2000 and 2010 editions; taken from
  secondary sources, not yet checked against the manual),
  (C − g)² / 2C, for someone arriving at a random time, where C is the cycle
  length and g the usable walk time. The values assume a 120 s cycle, about 11 s of
  walk time across the main road (a short walk interval plus some of the
  flashing phase), and about 60 s alongside it. These timings are
  assumptions, not measured; where a city's signal timing is known it
  should replace them. The formula ignores walkers who cross against the
  signal.

  Where a signalised crosswalk is mapped, the walker pays on it, once for
  each crosswalk used, so crossing two streets at a corner pays twice.
  A walker arriving at an intersection along a road pays once on entering
  it, whether or not they cross there, unless they arrive at the end of a
  mapped crosswalk, which charges instead. Arriving on a side street counts
  as crossing the main road, and arriving on the main road as crossing the
  side street. Footways and paths reaching an intersection pay nothing on
  entry.

  A road tagged `sidewalk=separate` has its sidewalks drawn as their own
  ways, so a walker on the road link itself is in the carriageway. At an
  intersection with mapped crosswalks, each such road link the walker takes
  into or inside the intersection pays the across-a-main-road wait (or the
  plain wait where there is no main road), so walking the carriageway is no
  way round the crosswalks.
- **Sidewalks drawn beside the street.** OpenStreetMap often draws a
  street's sidewalks as their own footways and leaves the street's own line
  untagged (or tagged `sidewalk=no`, as was once the practice). A street
  lined by such sidewalks along at least 80% of its length, within 25 m and
  running within 45° of the same way, is read as `sidewalk=separate`, for every walking
  preset. Sidewalks matched to one piece of a named street count for every
  piece of that name, since one sidewalk way often runs beside several.
  A local sidewalk layer's word stands. A sidewalk on one side counts, as a
  tagged one-sided sidewalk does; crossing to it is not charged. Within 3 km
  of Los Altos, using OpenStreetMap only, this covered about 56 of the 136 km of collectors and major roads
  whose own lines had no sidewalk tag, among them Almond Avenue and North
  San Antonio Road, which the walking screens had ranked first for a
  sidewalk they already have.
- **Sidewalk evidence.** Distance multipliers:
  - mapped sidewalk, local layer or path: × 1;
  - local street with unknown sidewalks: × 1.25;
  - other link with unknown sidewalks: × 2.
- **Links the preset removes.** It does not penalise these; it leaves them
  out of the walking network:
  - collector (tertiary) and major streets with unknown sidewalks;
  - primary, secondary or tertiary streets that OSM tags as having no
    sidewalk;
  - crossings mapped as unmarked or uncontrolled.

  With OpenStreetMap input only signalised crosswalks become crossing
  links, so the third case does not occur. This is the old
  study's rule, kept so the port reproduces its results. It goes against the
  plan that presets penalise stressful links and never remove them; the
  literature presets will penalise instead.
- **`walk/shortest`** is a baseline for comparison, not a model of
  walking. It takes the shortest route by distance over every walkable link,
  including those `walk/legacy` leaves out for missing or unknown sidewalks,
  with the same speed and signal waits, which set travel times but not the
  route. It is the walking counterpart of `bike/shortest`.
- **Crossing links recorded as `crossing=no`** (from local data) are left
  out by every walking preset, `walk/child` included: the tag means crossing
  is not allowed there, which is no matter of stress. From OpenStreetMap a
  crossing way without a signal, whatever its `crossing` tag, becomes an
  ordinary path, as above.

## Cycling (`bike/legacy`, `bike/shortest`, `bike/legacy-hills`)

Hand-set for the same study, and not estimated from observed route choice.

- **Speed: 10 mph** on the flat. Hand-set.
- **Hills** (`bike/legacy-hills` only; otherwise the same as
  `bike/legacy`). Speed is multiplied by the ratio of Parkin and
  Rotheram's speed on the grade to their speed on the flat (Parkin, J. and
  Rotheram, J. 2010, "Design speeds and acceleration characteristics of
  bicycle traffic for use in planning, design and appraisal", Transport
  Policy 17(5), 335–341, Table 2): 6.01 m/s on the flat, 0.2379 m/s faster
  per 1% down and 0.4002 m/s slower per 1% up. That gives × 1.12 at 3% down,
  × 0.80 at 3% up and × 0.53 at 7% up. Grades are held to the range in
  their data, 8.39% down to 9.34% up, because the straight line reaches
  zero at 15% up; steeper climbs are slower than this. Their data are 518
  speeds of 16 experienced commuters in Leeds, UK, taken at the end of each
  start from rest, so they may not hold for children or casual riders.
  The stress multiplier applies to the longer time uphill, so a stressful
  climb costs more than the same street downhill.
- **Signal waits**, charged each time a route enters a signalised
  intersection. Signal nodes within 35 m of each other, up to 70 m across,
  count as one intersection.
  - **Arriving on a side street at a main-road signal: 40 s**, as crossing
    the main road. From the same Highway Capacity Manual formula, with an
    assumed 120 s cycle and about 25 s of side-street green.
  - **Arriving on the main road: 15 s**, with about 60 s of green.
  - **At a signal with no main road: 20 s**, hand-set by the old study.

  A link does not know whether the rider turns, so a right turn from a side
  street onto the main road pays the 40 s too. A path arriving at the end of
  a mapped signalised crossing pays nothing on arrival: the crossing charges.
  A rider who arrives there and then goes on without using the crossing
  pays nothing; charging exactly needs costs per turn, which only the
  Broach presets use so far. The same holds for walkers arriving along a
  road at a crosswalk's end.
- **Traffic stress (LTS 1–4).** The rules are simplified from Mekuria, Furth
  and Nixon (2012):
  - **LTS 1:** separated tracks and paths, and streets at 25 mph or less with
    2 lanes or fewer and low traffic;
  - **LTS 2:** bike lanes at 30 mph or less, 3 lanes or fewer and no more
    than moderate traffic; streets in mixed traffic at 30 mph or less with 2
    lanes or fewer and no more than moderate traffic;
  - **LTS 3:** up to 35 mph, 3 lanes and high traffic;
  - **LTS 4:** anything higher.

  A limit is read to the nearest 5 mph band, as Furth's tables are, so
  50 km/h (31.1 mph) is a 30 mph street.
- **Traffic volume.** It is not known. A band is inferred from road class,
  lanes and speed:
  - motorway or trunk: very high;
  - primary, 4 or more lanes, or 40 mph or more: high;
  - secondary, tertiary, 3 lanes, or 35 mph or more: moderate;
  - residential: low;
  - service: very low.
- **Traffic counts in LTS.** The LTS criteria use volume once: in mixed
  traffic on a street of up to 3 lanes, no marked centreline and under 3,000
  vehicles a day lowers the rating one level (LTS 1 rather than 2 at 25 mph,
  2 rather than 3 at 30 mph). Furth calls the 3,000 threshold somewhat
  arbitrary, and volume plays no part on multilane roads or with bike
  lanes. So a counted street without a bike lane (or track or path), with
  up to 3 lanes and a speed of 30 mph or less, follows Furth's table:
  LTS 1 at 25 mph under 3,000 a day and LTS 2 at 3,000 or more; LTS 2 at
  30 mph under 3,000 and LTS 3 at 3,000 or more. Its traffic band is low
  under 3,000 and at least moderate otherwise; higher bands still come from
  road class, lanes and speed, since the criteria give no volume threshold
  above 3,000. Uncounted streets keep the rules above, which rate a 30 mph
  two-lane street with moderate inferred traffic LTS 2. Centrelines are not mapped, so a quiet counted
  street is assumed to have none. Sources:
  Mekuria, Furth and Nixon (2012), and Furth's criteria tables (2014,
  version 1.0). `bike/furth` uses the current tables, version 2.2, in full
  (below).
- **Traffic counts.** With `area.traffic_counts: hpms` (the default), the
  annual average daily traffic each state reports to FHWA's Highway
  Performance Monitoring System is read for the area, from the latest year
  FHWA publishes. HPMS covers federal-aid roads (arterials and major
  collectors and up) and few local streets. A street takes the count of the
  nearest HPMS section within 15 m of its midpoint running within 20° of
  its direction, so a cross street does not take the count of the road it
  meets. One-way streets reach 30 m, because OSM draws each side of a
  divided road as its own one-way line, often 15–25 m from the counted
  centre line; a one-way street 15–30 m from a parallel counted road would
  wrongly take its count. Only streets open to general traffic are matched (not service
  roads, parking aisles, cycleways or footways). Counts are two-way totals;
  each carriageway of a divided road gets the road's total. Off the state
  highways many HPMS figures are older local counts that the state grows
  forward each year. An override can set `traffic_aadt` on a street; it is
  credited to the override. The run log gives the share of street length
  with a count, by road class.
- **Missing speed and lanes.** Defaults by road class (`ROAD_DEFAULTS` in
  `network/bicycle.py`): residential streets 25 mph with 2 lanes; primary
  streets 35 mph with 4 lanes.
- **Stress multipliers.** Distance × 1, 1.25, 3 or 8 for LTS 1 to 4.
  Hand-set, using LTS as the scale. `shortest` uses × 1 throughout,
  as a shortest-distance comparison.
- **Crossing penalties.** The preset sets 15 s at a crossing of unknown
  control and 30 s at an uncontrolled one. They apply only to crossing
  links. With OpenStreetMap input only signalised crosswalks become crossing
  links, and those cost nothing extra, so these are never charged.

## Cycling on Furth's criteria (`bike/furth`)

As `bike/legacy-hills`, but traffic stress levels come from Furth's
"Level of Traffic Stress Criteria for Road Segments", version 2.2 (May
2022), the current version of the tables
([PDF](https://peterfurth.sites.northeastern.edu/files/2014/05/LTS-Tables-v2.2.pdf)).
Every street gets both levels: `lts_best` (the rules above) and
`lts_furth`, with `lts_furth_defaults` naming what was assumed. Any
`lts_factors` preset can read Furth's with `lts_criteria: furth`.

- **Mixed traffic** by through lanes each way, daily traffic and speed. With
  one lane each way it matters whether there is a centre line: an unlaned
  street at 25 mph stays LTS 1 up to 1,500 a day, one with a centre line
  only up to 1,000, and is LTS 3 above 1,500. One-way streets have their own
  rows, narrow and wide. Two lanes each way are LTS 3 at 25 mph, and LTS 4
  from 30 mph above 8,000 a day.
- **Bike lanes** by lanes each way, width, parking beside them and speed.
  Traffic volume plays no part, but where the mixed-traffic level is lower
  it is used. A lane under 4 ft wide, or beside parking with a reach under
  12 ft, counts as mixed traffic; beside parking only the reach is tested,
  as in the tables.
- **Tracks and paths** are LTS 1, as in the original criteria.
- **Shoulders** use the bike-lane table, without parking, where their width
  is tagged and at least 4 ft; otherwise the street is mixed traffic. The
  legacy rules ignore shoulders.
- **Speed.** The posted limit stands in for Furth's prevailing speed.
- **Not covered:** Furth's tables for right-turn lanes and unsignalised
  crossings (Mekuria, Furth and Nixon 2012, Tables 5 and 6; not updated
  since). The crossing and signal waits above stand in for them. The tables'
  "frequently blocked" bike lanes are not detected.

OpenStreetMap rarely records what the tables ask for. In Los Altos, widths
are almost never tagged, parking on about 10% of streets and
`lane_markings` on about 7% of residential ones; traffic counts cover
nearly all primary and secondary roads, 71% of tertiary roads and 4% of
residential streets (by length). Missing values take stated defaults,
which lean to the more stressful reading:

| Missing | Default |
|---|---|
| Daily traffic | residential and living streets 1,000; service 500; unclassified 2,000; tertiary 4,000; secondary 10,000; primary 20,000; trunk 30,000 (judgments; counted tertiary roads in Los Altos have a median near 5,800) |
| Speed, lanes | the road class defaults above |
| Centre line (`lane_markings`) | streets with a centre turn lane, and tertiary roads and above, have one; smaller streets do not |
| Lanes each way, centre turn lane | `lanes:forward` and `lanes:backward` (the busier direction counts), with any lanes left over a turn lane; `lanes:both_ways`; without these, a two-way street with an odd number of lanes has a centre turn lane |
| Parking beside the bike lane (`parking:*`, `parking:lane:*`, read on the bike lane's side; where only one side has it, both tables are used and the worse wins) | yes on local and tertiary streets; no on busier roads |
| Bike-lane width (`cycleway:*:width` plus `cycleway:*:buffer`, narrowest side) | under 6 ft; below 4 ft counts as mixed traffic (the 3.5 ft minimum beside a road edge is not used, as curbs are not read) |
| Bike lane plus parking ("reach") | from the two widths when both are tagged (under 12 ft counts as mixed traffic); otherwise under 15 ft |
| One-way street width (`width`) | narrow |

So an untagged residential street at 25 mph is LTS 1, and an uncounted
tertiary road at 30 mph with a centre line is LTS 3 (the legacy rules give
LTS 2). An untagged one-way street has half its class's default lanes, at
least one. A one-way carriageway of a divided road takes the whole road's
count, so its stress is overstated. A contraflow bike lane is rated like any
bike lane on the street, since one level serves both directions.

## Children (`walk/child`, `bike/child`, `walk/broach-2016-child`, `bike/broach-2011-child`)

No route-choice model estimated for children was found, so all four
presets are assumptions: `walk/child` and `bike/child` are built on the
legacy presets, and the `broach` child presets on the Portland models
(see below). Children are taken to avoid
stressful streets more than adults do, but never to refuse them: no street
is left out for stress, so a child still uses a short busy stretch when the
way round is much longer. Mapped traffic stress and sidewalks can be out of
date, and a ban can cut a whole neighbourhood off. Access rules still
apply: ways closed to walkers or bikes, one-way rules for bikes, and,
for walking, crossing links where crossing is not allowed.

- **Speeds** come from one GPS study of Dutch children aged 5 to 11 on
  school trips (Dessing, de Vries, Graham and Pierik 2014, BMC Public Health
  14, 227, Table 2). The averages were door to door, over short trips, with
  stops included:
  - walking: 4.1 km/h on average over 357 m. `walk/child` uses 2.5 mph
    (4.0 km/h), against 3 mph for adults;
  - cycling: 6.8 km/h (4.2 mph) on average over 624 m. `bike/child` uses
    6 mph, a judgment: waits at signals and crossings are modelled
    separately, so the door-to-door figure would count them twice. Adults
    use 10 mph.

  Older children ride and walk faster than this, so for a middle or high
  school the presets understate reach.
- **Hills** slow both, as in the `legacy-hills` presets, as a ratio to the
  flat speed. The grade relations come from adult data.
- **Cycling stress** multiplies distance by 1, 1.5, 6 and 20 for traffic
  stress levels 1 to 4, against 1, 1.25, 3 and 8 for adults: about twice
  the adult penalty on levels 3 and 4. The levels are Mekuria, Furth and
  Nixon's (2012), and Furth describes level 1 as suitable for children
  (Level of Traffic Stress page,
  https://peterfurth.sites.northeastern.edu/level-of-traffic-stress/), so
  only level 1 keeps the adult factor; the factors are judgments. Crossing costs and signal waits are as for
  adults.
- **Walking without sidewalks** multiplies distance, on links with unknown
  sidewalk or crossing evidence, by 1.5 on local streets, 4 on collectors
  and on other links (for example a crossing of unknown control), and 12 on
  major roads. Adults pay 1.25 on local streets and 2 on other links, and
  leave collectors and major roads out. Mapped sidewalks and paths cost
  × 1 for both.
- **Problematic links**, which adults leave out, cost a child the same as
  an unknown link of their class: a street a local survey records as
  having no sidewalk, a primary, secondary or tertiary road OpenStreetMap
  tags as having none, and a crossing mapped as unmarked or uncontrolled.
  Many quiet residential streets have no sidewalk, and children do walk
  them.

### The Broach child presets

`bike/broach-2011-child` and `walk/broach-2016-child` keep the adult
Portland models (see their sections) and change three things, all
judgments:

- **Speeds** as above: 6 mph cycling, 2.5 mph walking.
- **Stress terms doubled** (`stress_scale: 2`), as the legacy child presets
  roughly double the adult penalties on stressful streets. Cycling: the
  terms for roads of 10k+ vehicles a day without a bike lane, and for turns
  and crossings without a signal. Walking: the busy-street term and the
  terms for crossings without a signal or marking. Distance, turns, hills
  and facility bonuses are as for adults.
- **Walking without sidewalks**: the sidewalk term of
  `walk/broach-2016-sidewalks` (see the walking section) is doubled like
  the others. Links on collectors and major roads without sidewalk
  evidence, including problematic ones, count as 2 times their length;
  local streets, paths and crossings as 1, since many quiet residential
  streets have no sidewalk and children walk them. With it, the walking
  sidewalk screen runs for this preset.

On the flat, a busy road without a sidewalk then costs a child walker
about 2.3 times its length. The terms add, so a steep climb on such a road
reaches about 3.3 times. That is in the range of the published adult
models' largest terms: about 2 times for a steep climb walking, and about 3
to 3.5 times cycling on a road of 30k+ vehicles a day or a climb over 6%. The legacy child presets go much further (12 times on a
major road walking, 20 times on LTS 4 cycling); nothing published supports
factors that large.

Cycling hills cost as in the model and do not slow the child, as for
`bike/broach-2011`; walking hills slow travel time as for
`walk/broach-2016`.

## Cycling: Broach, Gliebe and Dill (`bike/broach-2011`, `bike/broach-2011-commute`, `bike/broach-2011-lanes`)

A route-choice model estimated from GPS traces of 162 Portland, Oregon
cyclists in 2007 (1,449 trips): Broach, J., Gliebe, J. and Dill, J. (2011),
"Bicycle route choice model developed using revealed preference GPS data",
90th Annual Meeting of the Transportation Research Board, Table 2. The
coefficients are used as printed, and the tests recompute the paper's Table 3
from them. The journal version (Transportation Research Part A 46(10),
2012) was not available to check and may differ.

- **How it costs a route.** In the model, distance enters as a logarithm,
  so every other term is worth a share of the trip's distance. Each term
  becomes extra distance on the link where it happens: b/b_d times the
  link's length for a share-of-length term, or b/b_d miles per event for a
  per-mile count (b is the term's coefficient, b_d the distance
  coefficient). This first-order form is exact for small shares and counts.
  It understates a route that is mostly steep or busy, because the model is
  exponential in them. The extra distance is costed at 10 mph.
- **What the terms are worth** on everyday trips, as extra distance:

  | Term | Everyday | Commute |
  |---|---:|---:|
  | Climbing 2–4%, per metre of link | +0.55 m | +0.32 m |
  | Climbing 4–6% | +1.36 m | +0.79 m |
  | Climbing 6% or more | +2.49 m | +1.45 m |
  | Road with 10–20k vehicles a day, no bike lane | +0.20 m | +0.31 m |
  | 20–30k, no bike lane | +0.86 m | +0.88 m |
  | 30k or more, no bike lane | +1.97 m | +2.10 m |
  | Bike boulevard | −0.20 m | −0.11 m |
  | Bike path | −0.30 m | −0.17 m |
  | Signal | +57 m | +33 m |
  | Stop sign | +15 m | +9 m |
  | Crossing a 5–10k road without a signal | +112 m | +65 m |
  | Crossing a 10–20k road without a signal | +159 m | +92 m |
  | Crossing a 20k+ road without a signal | +774 m | +450 m |
  | Turn, left or right | +114 m | +66 m |
  | Left turn off a 10–20k road without a signal, added | +241 m | +140 m |
  | Left turn off a 20k+ road without a signal, added | +577 m | +335 m |
  | Right turn onto a 10k+ road without a signal | +104 m | +61 m |

- **Traffic volumes** come from counts where there are any, and from road
  class elsewhere. The counts are federal HPMS daily traffic (see "Traffic
  counts" below) or a reviewed override. Without a count: tertiary 5–10k a
  day, secondary 10–20k, primary 20–30k, trunk and motorway 30k and more,
  and a tertiary or busier road with 4 or more lanes is at least 20–30k.
  A link road (`primary_link` and the like: a slip lane or turning
  roadway) carries only the traffic turning through it, so without a count
  one off a tertiary, secondary or primary road is under 5k, and a trunk or
  motorway ramp 10–20k. Given its road's band, crossing a right-turn slip
  lane at the corner cost as much as crossing the main road without a
  signal (about 3 minutes beside a primary road).
  These class bands are assumptions. Checked against HPMS counts around
  Los Altos, they held for tertiary streets (median 7,200 a day, 81% of
  length under 10k) but were too high for many primary and secondary
  roads: a third of counted primary length carried 10–20k, and over a
  third of secondary length under 10k.
- **Bike lanes and tracks** remove the traffic term, as bike lanes do in the
  model. The model has no term of its own for separated tracks, and
  Portland's riders could not tell paint from protection, so two
  assumptions are added (see "Painted and protected lanes" below).
- **Bike paths** are cycleways and paths that allow bicycles. Footways that
  allow bicycles are not, because they are often sidewalks; the model left
  out sidewalks and minor park paths.
- **Turns.** Turn and crossing terms depend on the movement through a
  node, so the search runs on movements ("arrived at this node from that
  one"), and a route's cost and its share of trips include them. Only a
  node where three or more ways meet has turns. The heading is measured
  over the 10 m of each link next to the node; a change of 45° or more is a
  turn, left or right. The paper found turns from street names and
  geometry; here it is geometry alone, so a street that turns a corner
  under one name counts as a turn. Turning straight back is not allowed.
  Every street meeting a node counts towards the three ways, including
  ones bikes may not use, so crossing them is still charged. Alternative
  routes may not pass through the same node twice; the single best route
  may, if going round a block is cheaper than a left turn. As elsewhere,
  the graph keeps one link per ordered pair of nodes, the cheaper, so of
  two parallel links the other's turns are never tried.
- **Left turns across traffic.** A left turn at a node without a signal off
  a road carrying 10–20k or 20k+ a day adds the paper's left-turn term to
  the turn. The paper's wording is "parallel traffic volume"; this tool
  reads it as the road being left.
- **Unsignalised crossings.** Going straight on or turning left at a node
  without a signal, where a busier road meets the one ridden, pays the
  crossing term for the busiest such road. A right turn pays the smaller
  right-turn term instead, if the road turned onto is busier than the one
  ridden and carries 10k or more, as in the paper. Links with the same street name as the one ridden
  count as the same road, so a change of count or road class along a
  street is not a crossing; an unnamed link is a road of its own. A
  signalised crosswalk on either side of the movement counts as a signal.
  A divided road, drawn as two one-way lines, is crossed once: each
  carriageway is half a crossing where the other is 60 m or less along the
  link ridden to or from the node. Walking follows the same rule. Nothing is charged where a route starts or
  ends.
- **Signals.** The wait stays in travel time, so time limits match the other
  presets. In the cost, the model's signal term replaces the wait, because
  the model's term already includes the delay.
- **Junctions drawn as several nodes.** A signal on a divided road, or a
  big intersection, is often mapped as several nodes joined by short links.
  The search treats each signal's nodes as one junction: a route entering
  it remembers the link it came in by, moves between its nodes with no
  turn or crossing cost, and pays one movement on leaving, judged from the
  link it entered by to the link it leaves by. Judged node by node, a right
  turn could be ridden as two "lefts" through the short links, or a
  straight crossing could count as two turns. Leaving back along the
  entering link is not allowed.
- **Slow signals.** The model's signal term is the same for every signal.
  A `signal` correction can say a light holds riders longer than usual
  (`extra_wait_s`); that wait is added to the cost of every passage
  through it, except a right turn where right on red is allowed. Right on
  red is taken as allowed, as in most of the US, unless OSM
  (`red_turn:right=no`, or `red_turn:right:bicycle=no`) or the correction
  says otherwise. Travel time keeps the ordinary signal wait.
- **Stop signs** are charged on entering a stop node, in both directions,
  because OpenStreetMap rarely says which approaches a stop controls.
- **Not yet used:** the bridge terms, which describe Portland's river
  crossings. The signal term is still charged on right turns at signals,
  which the paper's term leaves out, because signals are costed on links.
- **Splitting trips.** The model also sets how trips split between routes:
  a route's share goes as its path size to the power 1.81 times its cost to
  the power −5.22 (−8.98 for commutes), relative to the others. The cost
  includes the ride to the network. The study's `route_choice` search
  limits still decide which routes are compared.
- **Speed and waits** for travel time are as in `bike/legacy`. Hills do not
  change speed here, because the climbing terms already include the time.
- **Where it may not transfer.** Portland had a dense network of low-traffic
  bike routes. The authors doubt the very large penalties for busy roads
  would hold in places with fewer alternatives; there, people may not ride
  at all.

### Bike lanes valued like bike boulevards (`bike/broach-2011-lanes`)

In the model a bike lane only removes the busy-traffic term, so a lane on a
street under 10,000 vehicles a day is worth nothing. CyclePhilly riders
(see `validation/README.md`) went out of their way to quiet streets with
lanes, Spruce and Pine in Center City, that the model rates no better than
the quiet streets beside them. `bike/broach-2011-lanes` is
`bike/broach-2011` with one assumption added: every bike lane and separated
track also gets the model's bike boulevard term, so it counts as 20% shorter
than it is (on top of removing the traffic term on a busy street). The
coefficient is the paper's own and nothing was fitted to Philadelphia; it
is a sensitivity, not an estimate of what lanes are worth.

### Painted and protected lanes

Broach's model treats every bike lane alike: a lane removes the whole
busy-traffic term. Two settings, neither in the paper, separate paint from
protection.

- **A protected lane counts as 0.72 times the same street with a painted
  lane** (`protected_factor: 0.72`, every Broach cycling preset), apart from
  hills. The painted lane here is one that removes the whole busy-traffic
  term, as in the adult presets; a protected lane always removes all of it,
  so for children it beats paint by more (below). This is from Fosgerau, Łukawska, Paulsen and Rasmussen (2023),
  "Bikeability and the induced demand for cycling", *PNAS* 120(16),
  e2220515120, Table 4 (152,323 GPS trips in Copenhagen): on a road with
  two or more car lanes each way, a protected lane cost 0.772 per metre and
  a painted one 1.080, quiet residential streets being 1. Other studies
  bracket it: about 0.78 for Portland's bridges (Broach, Gliebe and Dill
  2011's bridge terms) and 0.63–0.68 in San Francisco (Fitch-Polse 2018,
  UC Davis dissertation, chapter 3, from few protected-lane trips). The
  factor applies to a track's whole cost, including any lane bonus.
- **For children, paint removes a quarter of the busy-traffic term**
  (`lane_traffic_relief: 0.25`, `bike/broach-2011-child` only); a protected
  lane removes all of it. US route-choice studies of adult riders find
  paint removes about all of the term (Broach, Gliebe and Dill 2011;
  Zimmermann, Mai and Frejinger 2017, about 90%) or more (Fitch-Polse
  2018). Copenhagen's much broader mix of riders valued paint on a large
  road at about 27% of the penalty, not significantly above zero (Fosgerau
  et al. 2023). Residents rating comfort on a four-lane 35 mph street put
  paint 45% of the way from no lane to a path, and a physical separation
  74% (McNeil, Monsere and Dill 2015, *Transportation Research Record*
  2520). Painted lanes also leave riders exposed to turning cars at
  intersections. No study of children's route choice was found, so 0.25 is
  a judgment, following the broader sample. Adult presets keep the paper's
  full relief.

On a 20–30k street for a child (with `stress_scale: 2`), that makes no lane
about 2.7 times the length, paint about 2.3 and a protected lane 0.72.

## Walking: Broach (`walk/broach-2016`, `walk/broach-2016-sidewalks`)

A route-choice model estimated from GPS traces of 283 Portland, Oregon
adults, 2010-2013 (1,167 walk trip stages): Broach, J. (2016), "Travel mode
choice framework incorporating realistic bike and walk routes", PhD
dissertation, Portland State University, Table 10, with the variables
defined in Table 9. The coefficients are used as printed; the tests
recompute the distance equivalents of Table 11 from them (the small terms
differ by a point or two, because Table 10 rounds to three decimals).

The model's utility is linear in distance, so every term is worth a fixed
distance, exactly:

| Term | Worth |
|---|---|
| Turn (30° or more onto a street of another name) | +54 m |
| Unsignalised crossing of a secondary road or above | +73 m |
| Crossing of a tertiary road with neither a marked crosswalk nor a signal | +28 m |
| Climbing at 10% or more | +100% of the distance |
| Alley, or a street with an unpaved surface | +53% |
| Tertiary road or above, or a sidewalk beside one | +13% |

Costs are this equivalent distance at the flat walking speed, 3 mph. Trips
split across routes with the model's own logit scale, 1 / (0.015 per m ×
walking speed), about 50 s, and path-size exponent, 0.128. The paper used
an "expanded" path size; the ordinary one stands in for it.

Mapping to OpenStreetMap, all assumptions:

- **Busy streets.** The paper's "collector or higher, about 10,000+
  vehicles a day" is read as tertiary roads and above, by class; traffic
  counts are not used. The model was estimated on street centre lines, so a
  sidewalk mapped as its own way (`footway=sidewalk`) takes the class and
  name of the street it runs beside: the nearest street cars may use,
  other than a parking aisle, driveway or drive-through, whose centre line
  is within 25 m of the sidewalk's midpoint and runs within 20°
  of it.
- **Crossings.** A mapped crossing way charges its own crossing, by the
  busiest road it meets. A crossing drawn in pieces, of one way or of ways
  meeting on the road they cross, charges once, shared by length. It counts
  as marked unless any of its ways, or its node on the road, is tagged
  `crossing=unmarked` or `crossing:markings=no`: a crossing way is usually
  mapped because it is painted. Elsewhere, at a node
  without a signal, walking on across a road of a third name crosses it,
  unmarked unless the node is tagged as a marked crossing. A crossing way
  that crosses two separate roads charges one crossing of the busier. Turning onto a road is taken as walking along it, on the near
  side.
- **Turns** are found from geometry at nodes where three or more ways meet.
  A sidewalk takes its street's name; an unnamed way is a street of its
  own, so a sharp bend where unnamed paths meet is a turn, except that
  unnamed tertiary and busier roads of one class count as one road. Stepping onto or
  off a crossing way is not.
- **Left out:** neighborhood commercial frontage (−28% along collector and
  minor arterial streets with shops), which is not known here, so busy
  shopping streets are a little costlier than in the model. Walking with a
  family member (+85% on distance, `together: true`) is off by default.
- **Sidewalks.** The paper tested missing sidewalks and found no effect,
  perhaps because the alleys and unpaved streets, which lack them, took it.
  So the preset charges nothing for sidewalk evidence, and walks streets
  known to lack one; access rules still apply. The legacy presets still
  weigh sidewalk evidence.
- **Signals.** The model has no term for signal delay. Waits stay in travel
  time, so time limits are as for `walk/legacy-hills`, but they are not in
  the cost. Hills slow walking in travel time as in `walk/legacy-hills`;
  the cost uses the model's climbing term instead.
- **Screens.** The sidewalk screen marks sidewalks as present, which this
  model does not value, so it is not run for this preset. It is run for
  `walk/broach-2016-sidewalks` and `walk/broach-2016-child`, which add a
  sidewalk term. The crosswalk and signal screens run for all three.

### With a sidewalk term on busier roads (`walk/broach-2016-sidewalks`)

A sensitivity, not a calibrated model. Broach's walking model has no
sidewalk term. One revealed-preference study that has one is a Toronto
model estimated the same way (a path-size logit on GPS-recorded walk
trips): Lue, G. (2017), "Estimating a Toronto Pedestrian Route Choice
Model using Smartphone GPS Data", MASc thesis, University of Toronto.

- **The estimate.** In its final model (Table 5.7; 776 trips by 71 adults,
  2014), length on streets with sidewalks on both sides is perceived as
  33% shorter than other length. Read the other way, length without a full
  pair of sidewalks counts as 1 / 0.67 = 1.5 times. The comparison group is
  everything else, including streets with a sidewalk on one side only and
  walkways. A separate "no sidewalk" term was significant in early versions
  but dropped, and its value is not reported.
- **Where it applies here.** Links on collectors (tertiary) and major roads
  with unknown or poor sidewalk evidence count as 1.5 times their length.
  Local streets, paths and crossings are left at 1. Toronto's model does
  not split by road class, so this is a judgment: in suburbs such as Los
  Altos most residential streets have no sidewalk, and people walk in the
  road there. In Toronto, streets with one or no sidewalk were mostly in
  low-density residential areas, so applying the term to local streets as
  well is also defensible; that would be a different preset.
- **Limits.** One small convenience sample of mostly downtown trips. Our
  sidewalk evidence mostly cannot tell one side from both, so a street with
  a sidewalk on one side counts as having sidewalks. The term is added to
  Broach's model, whose distance units it shares; Broach's busy-street term
  still applies on top.

## Driving (`car/*`)

Planning values, hand-set for a Los Altos school car-flow study, and not
estimated from observed delays.

- **Running speed.** 80% of the posted speed or, where none is posted, of a
  default for the road class: residential 25 mph, primary 35, service 10, and
  20 when no class matches.
- **Delays at intersections, in seconds:**

  | Preset | Signal | Stop | Right turn | Left turn | U-turn |
  |---|---:|---:|---:|---:|---:|
  | `central` | 20 | 5 | 3 | 7.5 | 27.5 |
  | `osrm-like` | 2 | 2 | 3 | 7.5 | 27.5 |
  | `higher-delay` | 35 | 15 | 5 | 10 | 30 |
  | `edge-time-only` | 0 | 0 | 0 | 0 | 0 |

  Yield signs add no delay: they are not read from the map.

  The `central` turn values approximate the OSRM car profile, whose angle
  penalty is capped at 7.5 s plus 20 s for a U-turn.
- **Turn types.**
  - Within 30° of straight is straight on, 150° or more is a U-turn, and
    anything between is a left or right turn.
  - Staying on a street of the same name counts as straight up to a right
    angle.
- **Signals and stops.** A signal is charged once per intersection: signal
  nodes within 35 m are grouped. Stop signs are not grouped, so two stop
  nodes close together charge twice. A stop sign is charged on every movement
  through its node, because OSM rarely says which approaches it controls.
- **Not modelled.** Congestion, parking and queues at drop-off.

## Splitting trips across routes (path-size logit)

Walking and cycling trips split across near-best routes. All values below
are hand-set and can be changed under `route_choice` in the study config.

- **Search:** up to 3 routes, found among the first 12 candidate paths.
- **Routes kept:** within 60 s and 10% of the best route's cost, and sharing
  at most 90% of their length with a route already kept.
- **Logit:** scale 60 s, path-size exponent 1.
- **Form:** a path-size logit, as used to estimate bicycle route choice by
  Broach, Gliebe and Dill. The `bike/broach-2011` presets use that model's
  own split instead (see above).

`routes: 1` gives each home a single best route.

## Homes and who travels

- **Counts.** 2020 Census Demographic and Housing Characteristics (DHC)
  block counts: residents, households, or residents in an age range. The age
  range is built from DHC age bands, taking a share of any band it only
  partly covers in proportion to the years covered.
- **Placing people.** Each block's count is shared among its buildings that
  could be homes (Overture Maps footprints), in proportion to floor area.
  - **Floors:** from the recorded floor count, else height ÷ 3 m rounded
    down, at least 1 and at most 60.
  - **Which buildings:** only those whose type is a home, plus untagged
    footprints of 40 m² or more.
  - **Blocks with no such building** keep one point.
- **Reaching the network.** A home joins the nearest street or path that
  the mode may use, however far, and then follows it to one of its two
  ends: whichever makes the whole trip cheaper, for each destination. The
  distance to the street plus the stretch along it is travelled at the
  mode's speed (10 mph for cars). When the two ends are compared, the
  stretch along the street is priced at that street's own cost per metre in
  the direction travelled, and an end reachable only against a one-way
  street is not used.
  - No home is taken to be cut off. Only streets in the largest connected
    piece of the mode's network count, so a home beside a loose end of the
    map (a parking aisle whose driveway is not drawn, a street cut at the
    edge of the area) joins the nearest street that leads somewhere, and
    there is no limit on how far that is. Homes are placed within Census
    blocks, so in a large block a few sit hundreds of metres from a street;
    they travel that distance at the mode's speed.
    A destination whose every entrance is on a loose end is then reached
    by no one; put an entrance on a connected street.
  - Joining the nearest node instead sent some homes the wrong way: a home
    40 m from its own street, whose corners were 130 m away, joined the end
    of a service road 116 m away that led out to another street.
  - The walk to the street is a straight line, so a home behind another may
    in fact be farther from it.
  - The route starts at the end chosen, so the turn or crossing there is
    not charged, as it was not when homes joined a node.
  - The stretch from the home along its own street to that end carries no
    flow on the map, as before; flow starts at the end.
  - Costs at points (signal waits, crossings, turns) are left out of the
    street's cost per metre, so they do not tip the choice of end.
  - The route's reported length, cost and streets run from that end; the
    stretch to it is in `snap_distance_m`.
  - Near-best alternative routes all leave from the chosen end; a route
    nearly as good from the other end gets no share.
  - The improvement screen keeps each home's end from the untreated
    network, so a treatment that would make the other end better is missed.
  - Checking routes against recorded trips (`validation/`) starts each trip
    end at the nearer end of its street, without the one-way or cost choice.
  - A street drawn in pieces that do not meet is measured in a straight
    line from where the home meets it to each end.

## Flows across the whole network (`network_flows`)

With `network_flows` in the config, a run also routes every home to many
destinations at once, for comparing modelled flows with street counts.

- **Destinations are clusters of jobs.** Jobs by 2020 Census block come from
  the Census Bureau's LEHD Origin-Destination Employment Statistics (LODES 8)
  workplace file, all jobs (`C000`), for the year in `jobs_year` (default
  2023). LODES covers jobs under unemployment insurance and federal
  employment, with noise added for confidentiality; the self-employed are
  missing. Each block's jobs sit at a point inside it.
- **Clustering.** The blocks are grouped into `destinations` clusters
  (default 80) by k-means weighted by jobs, seeded with weighted k-means++
  from a fixed seed, so a rerun gives the same clusters. Each cluster is a
  point destination at its member block nearest its job-weighted centre,
  with no entrances: trips end at its nearest node. Only blocks within
  200 m of a node on a street open to walking or cycling are used for that, so a
  cluster centred on a rail yard or an industrial site sits at its nearest
  member by a street. A cluster with no such member is left out of the
  routing, with a warning, and counted in the summary's `jobs_too_far`.
- **Jobs stand in for every trip end.** Shops, schools and services tend to
  cluster where jobs are, but this is an assumption, not an estimate. Home
  ends of trips are the study's demand weight, as for any destination.
- **Sharing each home's trips.** A production-constrained gravity model:
  a home's weight is split among the clusters whose best route fits the time
  cap, in proportion to jobs × the distance-decay factor at the route's
  length (the curve for `purpose`, default everyday). Every home that
  reaches at least one cluster sends all of its weight somewhere; nearer and
  larger clusters get more. With `distance_decay: none`, the split is by
  jobs alone within the cap.
- **Routes.** Each home-to-cluster trip is routed and split across near-best
  routes exactly as for a study destination. Cars are not included.
- **Not counts.** The result is a relative pattern of use for comparison,
  not a number of trips.

## Destinations and entrances

- Configured entrances (`access_points`, `car_access_points`) are snapped to
  the nearest node within 200 m. An entrance farther away is an error.
- **Without configured entrances:**
  - a point destination uses its nearest node;
  - an area destination uses the end nodes of links that cross its boundary,
    the inside end where there is one, so a long crossing link puts the
    entrance at its inside end;
  - cars use the configured walking and cycling entrances if there are any;
    otherwise, for an area, every car node inside it, and for a point (or an
    area with no node inside), the nearest street node.

## Local data and corrections

- **Local layers.** A city's sidewalks, signals, speeds or bikeways replace
  OSM values on matching streets.
  - **Matching distance:** sidewalks 18 m, signals 22 m, speeds 18 m,
    bikeways 14 m from the street. A line must also run within 30° of the
    street's direction.
  - **Signals** go to the nearest junction of 3 or more links.
- **Overrides.** Hand corrections, each with its reason and source, in a YAML
  file. See the README.

## Improvement screens and overlap

- **Treatments.** Each treatment is screened and ranked on its own, so a
  sidewalk is never compared with a signal in one list:
  - cycling, protected bike lane: a protected lane, which turns LTS 3–4
    into LTS 1, by either
    set of levels (for
    `bike/broach-2011`, which reads facilities, a separated track, which
    removes the traffic term and counts as 0.72 times its painted-lane cost).
    Streets with 10,000 or more vehicles a day (by count, else by road
    class) and no protected lane or path get it too, even at LTS 1–2: at
    25 mph a busy two-lane street rates LTS 2, yet Broach's model still
    charges riders for its traffic. Cuesta Drive in Los Altos (about 11,000
    a day, signed 25 mph) was left out of the screen until this was added;
  - cycling, signal or crossing beacon: at each junction without a signal,
    the unsignalised crossing and left-turn terms of `bike/broach-2011` are
    replaced by its signal term, where that is cheaper. Only the Broach
    presets have such terms, so only they get this screen;
  - walking, sidewalk: links along streets with unknown or poor sidewalk
    evidence become supported. Crossings are left alone;
  - walking, marked crosswalk: unsignalised crossings that are unmarked, or
    whose evidence is not supported, become marked and supported. In
    `walk/broach-2016` a marked crossing of a collector has no crossing
    penalty; an arterial keeps its penalty, since the model charges for
    crossing it unsignalised whether or not it is marked;
  - walking, signal or crossing beacon: unsignalised crossings of busy
    roads (tertiary and above) get a signal. The crossing penalty goes, and
    the preset's signal wait (`signal_wait_s`) is charged as at an existing
    signal: in travel time, and in cost too except in the `walk/broach-2016`
    presets, whose model has no signal-delay term.

  A beacon is costed as a signal; the models have no separate term for one.

  Places are whole named streets (each listed once, with the best of its three busiest
  sections), sections of unnamed or one-section streets, a crossing (all its
  pieces together, named after the street crossed and the nearest other
  street) or a junction (named after two streets that meet there). It ranks
  places by generalized minutes saved, weighted by the homes that use them
  (decayed, as above); the report and map call these "route-cost minutes". For presets with turn and crossing costs
  (the Broach ones), sections are found on the street links, but routes
  before and after are found on the preset's graph of movements, so those
  costs count: a protected lane that leads to an unsignalised crossing of a
  busy road saves only what is left after the crossing. A bike lane,
  sidewalk or crosswalk on a crossing way changes links; a signal at a
  junction changes the movements through it.
- **Overlap index.** sqrt(car percentile × the larger of the walk and bike
  percentiles), on each street. Tiers mark the top 1%, 5% and 10%. It shows
  where to look, not crash risk.

## Where routes meet traffic today

The improvement screen ranks places by the time a change would save, so it
finds barriers, often busy roads at the edge of a catchment, that few routes
use today. This list asks instead where routes run today and how risky each
place is. It follows the systemic safety approach, which screens a network
for features shared by crash sites instead of waiting for crashes (FHWA,
*Systemic Safety Project Selection Tool*, FHWA-SA-13-019, 2013; *NCHRP
Report 893: Systemic Pedestrian Safety Analysis*, 2018). It also follows Safe
Routes to School practice, which puts places children already use first
(National Center for Safe Routes to School, *Safety-based prioritization of
schools for SRTS infrastructure projects*).

Only each mode's first preset is listed, as for the screen. Streets and
crossings are ranked separately:

- **Streets:** neighbouring links of one street with the same points, for
  the same reasons, form a stretch. Its exposure is flow × length (person-kilometres) × points, and
  "people" is its busiest link's flow.
- **Crossings:** a route crosses at a mapped crossing way, or at a junction
  where it goes straight across a named road with 5,000 or more vehicles a
  day (band 1 or more), or turns left onto or off one (a turn of more than
  30°; traffic keeps right). Going along the road or turning right is not a
  crossing, and neither is a bend where two roads meet end to end. A road
  that changes name at a junction is one road there, called by both names:
  two roads with one leg each, within 30° of opposite ways. A crossing way that ends on the road it
  crosses counts once, as the crossing. Nodes within 60 m are one junction
  when they share two street names, or share one and are joined by a link
  of another name or none (the gap between a divided road's carriageways);
  a route through both counts once. Exposure is the people crossing ×
  points.

**Points.** The factors come from the guides above and the level of traffic
stress (Mekuria, Furth and Nixon 2012). The point values are ours, hand-set
and unfitted. These are the defaults; a study can change them (see "Changing
the points and the rule" below):

| Where | Factor | Points |
|---|---|---|
| Street, cycling | traffic stress 2 / 3 / 4 | 1 / 3 / 5 |
| Street, walking | no known sidewalk, traffic band 0 / 1 / 2+ | 1 / 2 / 4 |
| Street, walking | no known sidewalk and 35 mph or more | +1 |
| Street, both | in the top 10% of car flow to the same place (school drop-off), on a street with other points | +1 |
| Crossing, both | traffic band of the road crossed, 0–4 | 0–4 |
| Crossing, both | 35 mph or more / 4 or more lanes | +1 / +1 |
| Crossing, walking | no marked crosswalk on the map, on a road of band 1 or more | +1 |
| Crossing, both | signal | total × 0.25 |

Traffic bands are Broach's: under 5,000 vehicles a day, 5,000–10,000,
10,000–20,000, 20,000–30,000 and more. They come from a count where there
is one, else from road class. A mapped crossing takes its road's details
from the nearest road of the class it crosses within 30 m, so on a divided
road it sees one carriageway's lanes. At a junction of a divided road, the
lanes of the one-way carriageways crossed are added up. A junction is
signalised when it lies within a signal's footprint, as the route models
find it, so a signal mapped on the approaches counts. At a junction, a
stop sign is taken to be for the side street, so it does not lower the
points: the map data do not tell all-way stops apart.

**Tiers.** Fix first: 3 or more points, and at least 5 people or 5% of those
counted, whichever is smaller. Every other place with points is "watch".
Places with no points are not listed. Each place says in a sentence which
rule put it where it is.

**Changing the points and the rule.** Both are settings, under `exposure` in
the study config. Anything left out keeps the default above:

```yaml
exposure:
  points:
    bike_lts: {1: 0, 2: 1, 3: 3, 4: 5}          # by traffic stress level
    walk_no_sidewalk: {0: 1, 1: 2, 2: 4, 3: 4, 4: 4}  # by traffic band
    fast_mph: 35          # a speed limit at or over this is fast
    fast: 1
    drop_off_share: 0.10  # the busiest share of streets for car trips
    drop_off: 1
    crossing_band: {0: 0, 1: 1, 2: 2, 3: 3, 4: 4}
    wide_lanes: 4
    wide: 1
    unmarked: 1
    signal_factor: 0.25   # a signal multiplies a crossing's points by this
  fix_first:
    min_points: 3
    min_people: 5
    min_share: 0.05       # or this share of those counted, whichever is smaller
    busiest_share: null   # e.g. 0.10: see below
```

`busiest_share` adds a second rule for high use. With 0.10, the busiest 10%
of each list's places, by people, are "fix first" too, whatever their
points, as long as they meet the people bar. Their reason says so: "Fix
first: among the busiest 10% of places here (about 400 people use it),
though it has 2 risk points." Without it, a busy place with few points
stays "watch", and its reason says what it would need.

Each reason counts in the run's own words: the study's unit ("residents aged
11-13"), or "expected trips" when homes are weighted by distance. Figures are
whole numbers, with decimals only where a whole number would misstate how a
place compares with the rule.

The run records the values it used in `manifest.json`. The report prints
them under "Risk points used", and the notes under each list and on the map
state the rule, so a list can always be read against its own settings.
Changing the points is a judgement about risk; checking them against local
crashes is the better way to revisit them.

**All destinations together.** When a study has two or more destinations
(the schools of a district, say), the run also writes the lists with every
destination's routes added up, in `out/all-destinations/`. A place's people
are the sum over the destinations, and `people_by_destination` says how
many come from each (for a street, at its busiest link). Its tier is judged
on that sum, against everyone counted for all the destinations, so a street
used by a few children from each of three schools can be "fix first"
together when it is "watch" for each school. Risk points come from the same
factors, except that drop-off traffic is the destinations' car trips added
up. Destinations whose people are counted differently (children of
different ages, say) are added all the same, as people; keep destinations
that count something else, such as all residents for a downtown, in a study
of their own.

**What it is not.** Flows are modelled potential, not counts. The points
are not crash rates and have not been checked against crashes here.

## Not modelled

- trip chaining;
- school buses;
- transit;
- weather and darkness;
- personal differences beyond the age group;
- a dislike of climbing beyond the time it takes, except in the Broach
  presets (see above);
- unsignalised crosswalks, and the walking and cycling penalties for them,
  with OpenStreetMap input, except in `walk/broach-2016` (see above);
- actual signal timing (see the signal waits above);
- car congestion.
