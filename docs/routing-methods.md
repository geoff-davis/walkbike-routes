# Route-choice methods

Every route-choice method the project ships, where it comes from, what was
changed from the published version and why, and how far it has been
checked. The full detail of each assumption is in
[`assumptions.md`](assumptions.md); this page summarizes it and links to the
relevant section.

## What a preset is

A preset is a named, cited set of cost settings for one mode: how people
trade distance against busy streets, hills, turns and crossings. Each is a
YAML file in [`src/walkbike_routes/presets/`](../src/walkbike_routes/presets/)
with a `title`, a `source` and its `parameters`, and a study config lists the
presets to run for each mode ([choosing presets](adapting.md#6-choose-presets)).

Three rules hold for every preset:

- **Route choice and physical time are kept apart.** Each preset has an
  objective: generalized cost, physical length or travel time. Generalized
  cost adds the extra effort of busy streets, hills and crossings to clock
  time, and is reported in generalized minutes. Time limits (the walk or
  ride time a home must be within) always use physical travel time: speed,
  slowing on hills where the preset applies it, and signal waits.
- **Outputs are potential flow.** Flows show where trips would go if
  everyone within reach made them, weighted by national distance-decay
  shares. They are not counts of trips, forecasts or a measure of risk
  ([what a run counts](assumptions.md#what-a-run-counts)).
- **Coefficients from a paper are used as printed** and checked against it,
  and a test reproduces a small worked example from the paper where one
  exists.

## Summary

| Preset | Mode | Source | Modified from source? | Validated? |
|---|---|---|---|---|
| `bike/broach-2011` | bike | Broach, Gliebe and Dill (2011), Portland GPS | Yes: mapping to OSM, bridge terms dropped, protected-lane factor | Philadelphia recorded trips: closer than shortest on overlap, not on trips per street |
| `bike/broach-2011-commute` | bike | Same, commute terms | As `broach-2011` | Not routed in Philadelphia; paper's Table 3 reproduced |
| `bike/broach-2011-lanes` | bike | Same | As `broach-2011`, plus lanes get the bike-boulevard term | Best fit in Philadelphia, but chosen after seeing those trips |
| `bike/broach-2011-child` | bike | Same; speed after Dessing et al. (2014) | Stress terms doubled, paint removes a quarter of the traffic term, 6 mph (uncalibrated) | No |
| `bike/furth` | bike | Furth (2022), LTS criteria v2.2; speed on hills from Parkin and Rotheram (2010) | Hand-set stress multipliers, stated defaults (uncalibrated) | Philadelphia: further from riders than shortest |
| `bike/shortest` | bike | Baseline, no source | – | Philadelphia baseline |
| `walk/broach-2016` | walk | Broach (2016), Portland GPS | Mapping to OSM, commercial frontage left out, ordinary path size | Preliminary, weak check against Philadelphia counts |
| `walk/broach-2016-sidewalks` | walk | Same, plus a sidewalk term from Lue (2017), Toronto | Sidewalk term added on busier roads (uncalibrated) | No |
| `walk/broach-2016-child` | walk | Same; speed after Dessing et al. (2014) | Stress and sidewalk terms doubled, 2.5 mph (uncalibrated) | No |
| `walk/shortest` | walk | Baseline, no source | – | Preliminary Philadelphia counts baseline |
| `car/central`, `car/osrm-like`, `car/higher-delay`, `car/edge-time-only` | car | Hand-set planning values | – | No |

## Settings shared across presets

These are not part of any published route-choice model. They set travel
time for every walking or cycling preset, and route cost only where a
preset says so.

- **Flat speeds** are hand-set: 3 mph walking (2.5 mph for
  `walk/broach-2016-child`) and 10 mph cycling (6 mph for
  `bike/broach-2011-child`)
  ([walking](assumptions.md#walking-speed-waits-and-sidewalks-walkshortest),
  [cycling](assumptions.md#cycling-speed-waits-and-traffic-stress-bikeshortest)).
- **Signal waits** use the Highway Capacity Manual's average delay,
  (C − g)² / 2C, with an assumed 120 s cycle. Cycling: 40 s arriving on a
  side street at a main-road signal, 15 s arriving on the main road, 20 s at
  other signals. Walking: 50 s across a main road, 15 s across a side street
  at a main-road signal, 30 s elsewhere. The 20 s and 30 s values were
  hand-set in an earlier study. The presets say: "The timings are
  assumptions, not measured." The manual was "taken from secondary sources,
  not yet checked against the manual" (`assumptions.md`).
- **Hills.** Grades come from USGS 3DEP elevations, capped at 30%, and are
  left out on bridges, tunnels and links under 10 m
  ([street network](assumptions.md#street-network-openstreetmap)).
- **Splitting trips across routes.** Presets without a model of their own
  use a path-size logit with hand-set values: up to 3 routes, within 60 s
  and 10% of the best, logit scale 60 s, path-size exponent 1. The Broach
  presets use their model's own split
  ([path-size logit](assumptions.md#splitting-trips-across-routes-path-size-logit)).

## Cycling: Broach, Gliebe and Dill (2011)

`bike/broach-2011`, `bike/broach-2011-commute`, `bike/broach-2011-lanes`,
`bike/broach-2011-child`.

### Source

Broach, J., Gliebe, J. and Dill, J. (2011), "Bicycle route choice model
developed using revealed preference GPS data", 90th Annual Meeting of the
Transportation Research Board, Table 2: a path-size logit estimated on
1,449 GPS-recorded trips by 162 Portland, Oregon cyclists, 2007. The
published version (Transportation Research Part A 46(10), 1730-1740, 2012)
"was not available to check and may differ".

Added by the project:

- Protected lanes: Fosgerau, M., Łukawska, M., Paulsen, M. and Rasmussen,
  T. K. (2023), "Bikeability and the induced demand for cycling", PNAS
  120(16), e2220515120, Table 4.
- Children's speed: Dessing, D., de Vries, S. I., Graham, J. M. and Pierik,
  F. H. (2014), "Active transport between home and school assessed with
  GPS: a cross-sectional study among Dutch elementary school children", BMC
  Public Health 14, 227, Table 2.

### What it does

- **Objective:** generalized cost. The model's distance term is a
  logarithm, so every other term is worth a share of the trip's distance.
  Each becomes extra distance on the link or movement where it happens
  (b/b_d times the link's length, or b/b_d miles per event), costed at the
  preset's flat speed.
- **Penalized:** distance, turns, climbs of 2–4%, 4–6% and 6%+, signals,
  stop signs, roads of 10–20k, 20–30k and 30k+ vehicles a day without a
  bike lane, crossings and left turns at nodes without a signal by the
  traffic of the road crossed, and right turns onto a 10k+ road without a
  signal. **Rewarded:** bike boulevards and bike paths. On an everyday
  trip a turn is worth about 114 m, a signal 57 m, an unsignalised crossing
  of a 20k+ road 774 m, and a bike path takes off 0.30 m per metre
  ([table of terms](assumptions.md#cycling-broach-gliebe-and-dill-bikebroach-2011-bikebroach-2011-commute-bikebroach-2011-lanes)).
- **Trip type.** `broach-2011`, `-lanes` and `-child` use the everyday
  (non-commute) terms; `broach-2011-commute` adds the commute terms, which
  weigh distance more, so most terms are worth less extra distance, but the
  busy-traffic terms a little more.
- **Signals.** The wait stays in travel time; in the cost, the model's
  signal term replaces it, because "the model's term already includes the
  delay".
- **Hills** add the model's climbing terms to the cost and do not change
  travel time, "because the climbing terms already include the time".
- **Splitting trips:** the model's own path-size logit (path-size exponent
  1.81, cost exponent −5.22, or −8.98 for commutes).
- **Speed:** a flat 10 mph (6 mph for children), for travel time.

### Modifications from the published model

**Mapping to OpenStreetMap** (all assumptions, in
[`network/broach.py`](../src/walkbike_routes/network/broach.py)):

- **Traffic volume** comes from federal HPMS daily counts where a street
  has one, otherwise from road class: tertiary 5–10k, secondary 10–20k,
  primary 20–30k, trunk and motorway 30k+, a road with 4 or more lanes at
  least 20–30k, and slip lanes lower. The class bands "are assumptions";
  checked against HPMS counts in one area they held for tertiary streets
  but were too high for many primary and secondary roads.
- **Bike paths** are cycleways and paths that allow bicycles, not
  footways, "because they are often sidewalks; the model left out
  sidewalks and minor park paths". A **bike boulevard** is a street tagged
  `bicycle_road=yes` or `cyclestreet=yes`.
- **Turns** are found from geometry alone (45° or more over the 10 m next
  to a node where three or more ways meet); the paper used street names as
  well. The paper's "parallel traffic volume" for left turns is read as the
  road being left.
- **Junctions drawn as several nodes** count as one junction for turns.
- **Stop signs** are charged in both directions, "because OpenStreetMap
  rarely says which approaches a stop controls".
- **Not used:** the bridge terms, "which describe Portland's river
  crossings". The signal term is also charged on right turns at signals,
  which the paper's term leaves out, "because signals are costed on links".
- **Small-share form.** The first-order form "is exact for small shares and
  counts. It understates a route that is mostly steep or busy, because the
  model is exponential in them."

**Protected lanes** (every Broach cycling preset, "Not in the paper"). The
model treats every bike lane alike, and "Portland's riders could not tell
paint from protection". A protected lane (separated track) removes the
whole busy-traffic term and counts as 0.72 times the same street with a
painted lane (`protected_factor: 0.72`), from Copenhagen GPS trips
(Fosgerau et al. 2023: 0.772 against 1.080 on a large road). Other studies
cited in `assumptions.md` bracket it: about 0.78 from the model's own
Portland bridge terms, 0.63–0.68 in San Francisco
([painted and protected lanes](assumptions.md#painted-and-protected-lanes)).

**`broach-2011-lanes`: bike lanes valued like bike boulevards** ("Not in
the paper"). In the model a lane only removes the busy-traffic term, so on
a street under 10,000 vehicles a day it is worth nothing. The preset gives
every bike lane and track the model's bike-boulevard term as well, so a
lane counts as about 20% shorter. Why: it was "added after CyclePhilly
riders were seen to choose quiet streets with lanes, which the model values
no more than quiet streets without". "The coefficient is the paper's;
nothing was fitted." It "is a sensitivity, not an estimate of what lanes
are worth"
([details](assumptions.md#bike-lanes-valued-like-bike-boulevards-bikebroach-2011-lanes)).

**`broach-2011-child`: children's cycling** (uncalibrated). "No
route-choice model estimated for children was found", so all three changes
are judgments
([children](assumptions.md#children-walkbroach-2016-child-bikebroach-2011-child)):

- **Stress terms doubled** (`stress_scale: 2`): the busy-traffic terms and
  the terms for turns and crossings without a signal, "a judgment of
  roughly twice the adult penalty on stressful streets". Distance, turns,
  hills and facility bonuses are as for adults. No street is ruled out, so
  a child still uses a short busy stretch when the way round is much
  longer.
- **Paint removes a quarter of the busy-traffic term**
  (`lane_traffic_relief: 0.25`); a protected lane removes all of it. Why:
  "Copenhagen riders, a broader mix than Portland's, valued paint on a large
  road at about a quarter of the penalty and not significantly above zero
  (Fosgerau et al. 2023, Table 4), and lanes leave riders exposed to
  turning cars at intersections." On a 20–30k street, no lane then costs a
  child about 2.7 times the length, paint about 2.3 and a protected lane
  0.72.
- **Speed 6 mph.** Dessing et al. (2014) found 6.8 km/h (4.2 mph) door to
  door on short trips by Dutch children aged 5 to 11, with stops included.
  6 mph is a judgment because "waits at signals and crossings are modelled
  separately, so the door-to-door figure would count them twice". Older
  children ride faster, "so for a middle or high school the presets
  understate reach".

### Validation

**Worked examples from the paper.**
[`tests/test_broach.py`](../tests/test_broach.py) recomputes the paper's
Table 3 (the share of distance each term is worth, everyday and commute)
from the preset coefficients, and checks link and movement costs against
hand calculations. Other tests check that `-lanes` gives lanes the
boulevard term, that `-child` doubles only the stress terms, that a
protected lane counts as 0.72 of paint in every Broach preset, and that
paint removes a quarter of the traffic term for children.

**Philadelphia recorded trips**
([results](../validation/RESULTS.md#cycling-route-choice-against-recorded-trips)).
4,205 CyclePhilly trips by 164 riders (2014–16) in a 6 × 6 km area of
central Philadelphia were each routed from their first point to their last.
Nothing was fitted. 95% intervals resample riders.

| Measure | Ridden | `broach-2011` | `broach-2011-lanes` | `shortest` |
|---|--:|--:|--:|--:|
| Mean overlap with the ridden route | – | 0.54 (0.50 to 0.57) | 0.61 (0.56 to 0.65) | 0.50 (0.44 to 0.52) |
| Overlap better than shortest | – | 0.05 (0.02 to 0.09) | 0.11 (0.08 to 0.18) | – |
| Same route (overlap 0.8+) | – | 33% | 41% | 25% |
| Share on bike lanes | 30% | 19% | 25% | 19% |
| Share low stress (LTS 1–2) | 41% | 33% | 39% | 35% |
| Turns per km | 1.11 | 0.99 | 0.98 | 2.00 |
| Busy-street crossings per km | 0.13 | 0.14 | 0.12 | 0.18 |
| Trips per street, Spearman's ρ | – | 0.79 (0.74 to 0.80) | 0.82 (0.77 to 0.83) | 0.79 (0.74 to 0.81) |

- `broach-2011` routes are the most like riders' of the published models.
  It matches riders' rate of turns and busy-street crossings, "but on which
  streets the trips use it does no better than plain shortest paths".
- `broach-2011-lanes` is the closest on every measure above and "the only
  preset clearly better than shortest paths on trips per street". On Spruce
  and Pine, quiet one-way streets with lanes, it gives 538 and 500 trip-km
  against riders' 815 and 706 (`broach-2011`: 208 and 271), closing "about
  half the gap ... without adding turns".

**Caveats** stated in
[RESULTS.md](../validation/RESULTS.md#what-this-shows-and-doesnt):

- The lane bonus "was chosen after seeing these trips, so it still needs a
  test on other data"; another city's trips are "the obvious next check".
- One city, with a street grid. Most riders who described themselves were
  "Enthused & Confident" or "Strong & Fearless". Broach's model was
  estimated on similar riders, "so a good fit here is the easy case".
- The trips are from 2014–16 but the network is today's OpenStreetMap, and
  DVRPC blurred the trip ends.
- If the journal version of the paper "estimates a lane term of its own,
  that should replace the borrowed one".

**Not validated:** `broach-2011-commute` was not routed in Philadelphia.
`broach-2011-child` has not been checked against children's routes; "No
preset has been checked against children's routes"
([adapting.md](adapting.md#6-choose-presets)). The protected-lane factor
was not checked separately. The authors "doubt the very large penalties
for busy roads would hold in places with fewer alternatives".

## Cycling: Furth's level of traffic stress (`bike/furth`)

### Source

Furth, P. G. (2022), "Level of Traffic Stress Criteria for Road Segments,
version 2.2"
([PDF](https://peterfurth.sites.northeastern.edu/files/2014/05/LTS-Tables-v2.2.pdf)).
Speed on hills: Parkin, J. and Rotheram, J. (2010), "Design speeds and
acceleration characteristics of bicycle traffic for use in planning, design
and appraisal", Transport Policy 17(5), 335-341, Table 2.

### What it does

- **Objective:** generalized cost. Each street gets a traffic stress level
  from Furth's tables: mixed traffic by lanes, daily traffic and speed;
  bike lanes by lanes, width, parking and speed; tracks and paths LTS 1.
  Riding time is multiplied by 1, 1.25, 3 or 8 for LTS 1 to 4, and signal
  waits are added.
- **Hills** change riding speed by Parkin and Rotheram's relation (0.2379
  m/s faster per 1% down and 0.4002 m/s slower per 1% up from 6.01 m/s,
  used as a ratio to the flat speed), held within the 8.39% down to 9.34%
  up their data covered. The stress multiplier applies to the longer
  uphill time, so a stressful climb costs more than the same street
  downhill.
- **Crossings without a signal** add 30 s, or 15 s where control is
  unknown. With OpenStreetMap input only, these are never charged, because
  only signalised crosswalks become crossing links
  ([cycling](assumptions.md#cycling-speed-waits-and-traffic-stress-bikeshortest)).

### Modifications from the published criteria

- **The multipliers are not Furth's.** They are "a hand-set judgment, not
  estimated from observed route choice", using LTS as the scale.
- **Stated defaults where OpenStreetMap is silent.** Daily traffic, speed,
  lanes, centre line, parking, bike-lane width and reach take defaults that
  "lean to the more stressful reading": for example residential streets
  1,000 a day and primary roads 20,000 (judgments). Why: OSM "rarely records
  what the tables ask for"
  ([defaults table](assumptions.md#cycling-on-furths-criteria-bikefurth)).
- **Posted limit** stands in for Furth's prevailing speed.
- **Not covered:** Furth's tables for right-turn lanes and unsignalised
  crossings; "the crossing and signal waits above stand in for them".
  "Frequently blocked" bike lanes are not detected.

### Validation

- [`tests/test_furth.py`](../tests/test_furth.py) checks the tables "cell
  by cell against the published PDF", and that defaults are named and lean
  stressful. [`tests/test_grade.py`](../tests/test_grade.py) checks the
  Parkin and Rotheram speeds at −3%, 0, +3% and +7% and the clamp beyond
  the data.
- **Philadelphia:** mean overlap 0.42 (0.39 to 0.49), −0.08 (−0.12 to 0.02)
  against shortest; 18% same route; trips per street ρ 0.59 (0.56 to 0.62),
  −0.20 against shortest. It keeps to low-stress streets far more than
  riders (64% against 41%) and makes 3.12 turns per km against riders'
  1.11. RESULTS.md: "`bike/furth` [is] furthest from riders" with the now
  removed legacy preset.
- **Uncalibrated:** the multipliers were not fitted, and the Parkin and
  Rotheram data are 518 speeds of 16 experienced Leeds commuters, "so they
  may not hold for children or casual riders".

## Cycling: shortest distance (`bike/shortest`)

### Source

None: "A baseline for comparison, not a model of how people ride."

### What it does

- **Objective:** physical length. Every link a bike may use counts at its
  length, whatever its traffic stress. Speed (a flat 10 mph) and signal
  waits "set travel times but not the route".
- Access and one-way rules still apply
  ([street network](assumptions.md#street-network-openstreetmap)).

### Validation

It is the baseline the other presets are compared with in Philadelphia:
mean overlap 0.50 (0.44 to 0.52), 25% same route, trips per street ρ 0.79
(0.74 to 0.81), 2.00 turns per km. In a grid many routes tie on length, so
which tied street it takes is arbitrary.

## Walking: Broach (2016)

`walk/broach-2016`, `walk/broach-2016-sidewalks`, `walk/broach-2016-child`.

### Source

Broach, J. (2016), "Travel mode choice framework incorporating realistic
bike and walk routes", PhD dissertation, Portland State University, Table
10 (variables defined in Table 9): a path-size logit estimated on 1,167
GPS-recorded walk trip stages by 283 Portland, Oregon adults, 2010-2013.

Added by the project:

- Sidewalk term: Lue, G. (2017), "Estimating a Toronto Pedestrian Route
  Choice Model using Smartphone GPS Data", MASc thesis, University of
  Toronto, Table 5.7.
- Speed on hills: Tobler, W. (1993), "Three presentations on geographical
  analysis and modeling", National Center for Geographic Information and
  Analysis Technical Report 93-1.
- Children's speed: Dessing et al. (2014), as for cycling.

### What it does

- **Objective:** generalized cost. The model is linear in distance, so
  every term is worth a fixed distance, exactly: a turn 54 m, an
  unsignalised crossing of a secondary road or above 73 m, an unmarked
  crossing of a tertiary road 28 m; climbing at 10% or more +100% of the
  distance, alleys and unpaved streets +53%, tertiary roads and above (or a
  sidewalk beside one) +13%
  ([table](assumptions.md#walking-broach-walkbroach-2016-walkbroach-2016-sidewalks)).
  Costs are this distance at the flat walking speed.
- **Signals.** The model has no signal-delay term, so waits count toward
  time limits but are not in the cost.
- **Hills** slow travel time by Tobler's hiking function, as a ratio to its
  flat speed; the cost uses the model's climbing term instead.
- **Sidewalks.** `walk/broach-2016` charges nothing for sidewalk evidence:
  "The paper tested missing sidewalks and found no effect, perhaps because
  the alleys and unpaved streets, which lack them, took it."
- **Splitting trips:** the model's own logit scale, about 50 s at 3 mph,
  and path-size exponent 0.128.
- **Speed:** a flat 3 mph (2.5 mph for children), for travel time.

### Modifications from the published model

**Mapping to OpenStreetMap** (all assumptions, in
[`network/broach_walk.py`](../src/walkbike_routes/network/broach_walk.py)):

- **Busy streets.** The paper's "collector or higher, about 10,000+
  vehicles a day" is read as tertiary roads and above, by class; traffic
  counts are not used. A sidewalk mapped as its own way takes the class and
  name of the street beside it, because the model was estimated on centre
  lines.
- **Crossings.** A mapped crossing way counts as marked unless tagged
  unmarked, since "a crossing way is usually mapped because it is painted".
- **Turns** are found from geometry; a sidewalk takes its street's name.
- **Left out:** neighbourhood commercial frontage (−28% in the model), "which
  is not known here, so busy shopping streets are a little costlier than in
  the model". Walking with a family member (+85%) applies only with
  `together: true`.
- **Path size.** The paper's "expanded" path size is approximated by the
  ordinary one. Reason not recorded.

**`walk/broach-2016-sidewalks`: a Toronto sidewalk term** (uncalibrated,
"Partly an assumption"). Links on collectors and major roads without
sidewalk evidence count as 1.5 times their length. In Lue's Toronto model,
length with sidewalks on both sides was perceived as 33% shorter than other
length (0.00665 / 0.02), so other length counts as 1 / 0.67 = 1.5 times.
Local streets, paths and crossings stay at 1: Toronto's model does not split
by road class, so this "is a judgment for suburbs where most residential
streets have no sidewalk and people walk in the road". Limits: "One small
convenience sample of mostly downtown trips", and the sidewalk evidence
mostly cannot tell one side from both
([details](assumptions.md#with-a-sidewalk-term-on-busier-roads-walkbroach-2016-sidewalks)).

**`walk/broach-2016-child`: children's walking** (uncalibrated; "no
route-choice model estimated for children was found"):

- **Stress terms doubled** (`stress_scale: 2`): the busy-street term and
  the terms for crossings without a signal or marking.
- **Sidewalk term doubled:** collectors and major roads without sidewalk
  evidence count as 2 times their length (1 + 2 × 0.5); local streets,
  paths and crossings as 1, "since many quiet residential streets have no
  sidewalk and children walk them". A busy road without a sidewalk then
  costs a child walker about 2.3 times its length on the flat, which the
  repo puts "in the range of the published adult models' largest terms".
- **Speed 2.5 mph** (4.0 km/h), after Dessing et al.'s 4.1 km/h door to
  door for children aged 5 to 11.
- No street is ruled out.

### Validation

- **Worked examples.**
  [`tests/test_broach_walk.py`](../tests/test_broach_walk.py) recomputes
  the distance equivalents of the paper's Table 11 from the coefficients
  ("the small terms differ by a point or two, because Table 10 rounds to
  three decimals"), the +85% for walking together and the logit scale.
  Other tests check that the child preset doubles only the stress terms and
  charges for missing sidewalks, and that the sidewalk preset charges only
  busier roads without evidence.
  [`tests/test_grade.py`](../tests/test_grade.py) checks Tobler's function
  on the flat, at its fastest (5% down) and at 10% up.
- **Philadelphia counts (preliminary)**
  ([results](../validation/RESULTS.md#walking-flows-against-counts-preliminary)).
  Modelled walking flows were ranked against 308 DVRPC count sites:

  | | One sidewalk, as counted | Whole street |
  |---|--:|--:|
  | `walk/broach-2016` | 0.15 (0.04 to 0.26) | 0.32 (0.22 to 0.42) |
  | `walk/shortest` | 0.15 (0.04 to 0.25) | 0.23 (0.12 to 0.34) |
  | Street class alone | −0.10 | 0.35 (0.26 to 0.45) |

  The run was stopped "once it was clear that it tests demand more than
  route choice"; the plots and unmatched sites have not been reviewed. The
  whole-street comparison "was chosen after both were seen". On whole
  streets, "ranking sites by street class alone does as well as any
  preset". Counts from 2014 and earlier show no relationship, which "hasn't
  been explained". This "says little about route choice".
- **Not validated:** walking route choice has "not yet been checked against
  observed routes" ([README](../README.md#how-it-works)).
  `walk/broach-2016-sidewalks` and `walk/broach-2016-child` were not run in
  Philadelphia, and no preset has been checked against children's routes.
  Tobler's function was "Not checked against city walking".

## Walking: shortest distance (`walk/shortest`)

### Source

None: "A baseline for comparison, not a model of how people walk."

### What it does

- **Objective:** physical length. Every walkable link counts at its length,
  whatever its sidewalk evidence. Speed (a flat 3 mph) and signal waits set
  travel times but not the route; it walks at the flat speed on hills.
- Crossing links recorded as `crossing=no` are still left out, as by every
  walking preset, because the tag means crossing is not allowed there.

### Validation

The baseline in the preliminary Philadelphia counts check (table above).

## Driving (`car/*`)

`car/central`, `car/osrm-like`, `car/higher-delay`, `car/edge-time-only`.

### Source

"Planning-level values set by hand, not estimated from observed delays."
The `central` turn values "approximate the OSRM car profile, whose angle
penalty is capped at 7.5 s with 20 s more for a U-turn".

### What it does

- **Objective:** travel time. Running speed is 80% of the posted speed, or
  of a road-class default where none is posted, plus delays at
  intersections:

  | Preset | Signal | Stop | Right turn | Left turn | U-turn |
  |---|--:|--:|--:|--:|--:|
  | `central` | 20 s | 5 s | 3 s | 7.5 s | 27.5 s |
  | `osrm-like` | 2 s | 2 s | 3 s | 7.5 s | 27.5 s |
  | `higher-delay` | 35 s | 15 s | 5 s | 10 s | 30 s |
  | `edge-time-only` | 0 | 0 | 0 | 0 | 0 |

- A signal is charged once per intersection (nodes within 35 m grouped);
  a stop sign on every movement through its node, because OSM rarely says
  which approaches it controls. Yield signs add no delay.
- **Not modelled:** congestion, parking and queues at drop-off
  ([driving](assumptions.md#driving-car)).

Car flows feed the car-overlap layer, where car flow meets walking or
cycling flow ("where to look, not a risk measure").

### Validation

None against observed driving. Tests in
[`tests/test_cars_and_overlap.py`](../tests/test_cars_and_overlap.py) check
the mechanics (running speed, one-way streets, a left-turn delay sending a
car the long way round, grouped signals).

## Removed: the legacy presets

The `legacy` presets (`bike/legacy`, `walk/legacy`, `legacy-hills` and the
`child` presets built on them) were hand-set profiles: traffic-stress
multipliers for cycling, sidewalk evidence for walking. They
were removed after the Philadelphia checks
([RESULTS.md](../validation/RESULTS.md), which keeps their figures as run;
[#7](https://github.com/geoff-davis/walkbike-routes/pull/7)). On recorded trips `bike/legacy` was the furthest from
riders: mean overlap 0.34 (0.31 to 0.38), −0.16 against shortest, 10% same
route, trips per street ρ 0.51, 3.19 turns per km, 9% of its length on bike
lanes against riders' 30%, and busy-street crossings at 0.24 per km against
riders' 0.13. `walk/legacy` ranked the count sites no better than
`walk/shortest` (whole street ρ 0.22 against 0.23).

Street stress levels from the same hand-built rules (`lts_best`) remain,
and the stress screens still read them.

## What has not been validated

- Anything outside central Philadelphia. "No preset has been checked
  against counts or trips in your town."
- `bike/broach-2011-lanes` on data other than the trips it was chosen
  after.
- `bike/broach-2011-commute`, not routed in Philadelphia.
- Both children's presets. Their speeds come from one Dutch GPS study, and
  their doubled penalties are judgments.
- Walking route choice. The only walking check is a preliminary,
  demand-dominated comparison with counts, and the sidewalk and child
  walking presets were not in it.
- The protected-lane factor and the children's paint relief, beyond the
  published studies they come from.
- `bike/furth`'s stress multipliers, which are hand-set.
- All car presets.
- The assumed signal timings.
