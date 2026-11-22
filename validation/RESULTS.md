# What the Philadelphia checks found

Results of the two checks in this folder (see [README.md](README.md) for how
they work), run on the central Philadelphia study in November 2026. Only
aggregate figures are given here. Nothing was fitted to the trips or the
counts, but one preset, `bike/broach-2011-lanes`, was added after looking at
the trips (see below). The `legacy` presets, with `legacy-hills` and the
`child` presets built on them, have since been removed on these results;
their figures are kept as run.

## Summary

- **Route choice, cycling.** Of the published models, `bike/broach-2011`
  routes are the most like the routes CyclePhilly riders took. It matches
  riders' rate of turns and busy-street crossings, but on which streets the
  trips use it does no better than plain shortest paths.
- **Bike lanes.** Every published preset puts less of its routes on bike
  lanes than riders do (9–22% against 30%). In Broach's model, a lane only removes the
  penalty for busy traffic, so on a quiet street it is worth nothing. Riders
  go out of their way for quiet streets with lanes: Spruce and Pine.
- **A lane bonus helps.** `bike/broach-2011-lanes` gives every lane and
  track the model's own bike-boulevard term. It is the closest to riders on
  overlap, trips per street, and bike-lane and low-stress shares, and the
  only preset clearly better than shortest paths on trips per street. It was chosen after seeing these trips, so it still
  needs a test on other data.
- **`bike/legacy` and `bike/furth` are furthest from riders.** They zigzag,
  at three turns per km against riders' one. legacy uses few bike lanes and
  crosses busy streets twice as often. furth keeps to low-stress streets far
  more than these riders do.
- **Walking counts (preliminary).** Modelled walking flows rank DVRPC's
  count sites only weakly (ρ about 0.15–0.3). Counting both sidewalks and
  the road, no preset beats ranking sites by street class alone. That mixes
  route choice with crude demand (job locations standing in for every trip
  end), so it says little about route choice.

## Cycling route choice against recorded trips

**Data.** CyclePhilly trips, May 2014 to April 2016. 4,205 trips by 164
riders lie wholly inside the 6 × 6 km study area and pass the filters in the
README. 17 more had no route between their ends. Each trip was routed from
its first point to its last under each preset. 95% intervals come from
resampling riders, each with all their trips (2,000 draws).

### Overlap with the route ridden

Overlap is 1 when the modelled route and the ride are the same (within 20 m).
"Same route" is overlap of 0.8 or more.

| Preset | Mean overlap (95%) | Better than shortest (95%) | Same route |
|---|--:|--:|--:|
| `broach-2011-lanes` | 0.61 (0.56 to 0.65) | 0.11 (0.08 to 0.18) | 41% |
| `broach-2011` | 0.54 (0.50 to 0.57) | 0.05 (0.02 to 0.09) | 33% |
| `shortest` (shortest distance) | 0.50 (0.44 to 0.52) | – | 25% |
| `furth` | 0.42 (0.39 to 0.49) | −0.08 (−0.12 to 0.02) | 18% |
| `legacy` | 0.34 (0.31 to 0.38) | −0.16 (−0.19 to −0.10) | 10% |

The order is the same with 10 m and 30 m buffers. In every trip purpose and
every rider group riders chose for themselves in the app,
`broach-2011-lanes` scores highest and `broach-2011` beats shortest
distance.

### What the routes are made of

Shares of route length, and events per km, summed over all trips.

| Routes | Path or track | Bike lane | Low stress (LTS 1–2) | Turns per km | Busy-street crossings per km |
|---|--:|--:|--:|--:|--:|
| ridden | 12% | 30% | 41% | 1.11 | 0.13 |
| `broach-2011-lanes` | 14% | 25% | 39% | 0.98 | 0.12 |
| `broach-2011` | 12% | 19% | 33% | 0.99 | 0.14 |
| `shortest` | 12% | 19% | 35% | 2.00 | 0.18 |
| `furth` | 25% | 22% | 64% | 3.12 | 0.14 |
| `legacy` | 16% | 9% | 39% | 3.19 | 0.24 |

In a grid many routes tie on length, so a preset can want what riders want
and still pick a different street. This table asks whether the routes are
alike, not whether they are the same.

### Trips per street

Each street link's count of ridden trips, ranked against its count of
modelled trips (Spearman's ρ over the 19,852 links any route used). Summing
thousands of trips cancels the arbitrary choice between tied routes.

| Preset | ρ (95%) | Better than shortest (95%) |
|---|--:|--:|
| `broach-2011-lanes` | 0.82 (0.77 to 0.83) | 0.02 (0.01 to 0.05) |
| `broach-2011` | 0.79 (0.74 to 0.80) | −0.01 (−0.02 to 0.02) |
| `shortest` | 0.79 (0.74 to 0.81) | – |
| `furth` | 0.59 (0.56 to 0.62) | −0.20 (−0.22 to −0.15) |
| `legacy` | 0.51 (0.49 to 0.56) | −0.28 (−0.30 to −0.21) |

The streets where presets miss most, in trip-km:

| Street | Ridden | `broach-2011` | `broach-2011-lanes` | Shortest | `furth` | `legacy` |
|---|--:|--:|--:|--:|--:|--:|
| Spruce Street | 815 | 208 | 538 | 170 | 574 | 232 |
| Pine Street | 706 | 271 | 500 | 144 | 551 | 134 |
| Spring Garden Street | 1,146 | 1,033 | 1,292 | 993 | 247 | 134 |
| South Broad Street | 221 | 84 | 81 | 556 | 206 | 560 |
| Delaware River Trail | 44 | 288 | 284 | 21 | 675 | 166 |

### Spruce and Pine

Spruce and Pine are one-way streets through Center City with bike lanes
along almost all of their length, tagged correctly in OpenStreetMap, and
under 10,000 vehicles a day. Riders put 1,858 trip-km on Center City bike
lanes, against 851 for `broach-2011` and 765 for shortest paths.

- In Broach, Gliebe and Dill's model (2011 conference paper, Table 2) a
  lane has no value of its own. It only removes the penalty for roads with
  10,000 or more vehicles a day. To the model, Spruce and Pine cost the same
  as Locust Street or the quiet stretches of Chestnut and Walnut, so its
  routes spread across all of them. Riders pick the streets with lanes.
- It is not that the trips predate today's lanes: in the study's Center City
  stretch, Chestnut and Walnut have lanes on little of their length, and they
  still draw `broach-2011` routes.
- `bike/broach-2011-lanes` adds the model's bike-boulevard term to every
  lane and track, so a lane counts as about 20% shorter. The coefficient is
  the paper's, not fitted. It closes about half the gap on Spruce and Pine
  without adding turns.
- The journal version of the paper (Transportation Research Part A, 2012)
  was not available to check. If it estimates a lane term of its own, that
  should replace the borrowed one.

### What this shows and doesn't

- It is one city, with a street grid, and its riders chose to use a
  tracking app. Most trips by riders who described themselves were from
  "Enthused & Confident" or "Strong & Fearless" riders. It says nothing about children, about people who
  don't yet ride, or about suburban streets like Los Altos's, where most
  lanes are on quiet streets and the lane bonus would matter more.
- Broach's model was estimated on similar riders (Portland, 2007), so a
  good fit here is the easy case.
- The trips are from 2014–16, but the network is today's OpenStreetMap.
- DVRPC snapped the trips to its 2010 streets and blurred their ends, so the
  first and last blocks of each trip are uncertain.
- About 10 trips were checked by eye on a map before any figure was quoted.
- The lane bonus was chosen after looking at these trips. Testing it on
  another city's trips (Montreal publishes some under CC-BY 4.0) is the
  obvious next check.

## Walking flows against counts (preliminary)

A `network_flows` run sends each home's residents to 40 job clusters (from
Census LODES) by gravity, and the modelled flow at each DVRPC count site is
ranked against the count. The full run was stopped once it was clear that
it tests demand more than route choice, so the cycling flows are not
complete and these walking figures were not finished: the plots and
unmatched sites have not been reviewed.

ρ over 308 walking count sites inside the study area, with 95% intervals:

| | One sidewalk, as counted | Whole street |
|---|--:|--:|
| `walk/broach-2016` | 0.15 (0.04 to 0.26) | 0.32 (0.22 to 0.42) |
| `walk/legacy` | 0.16 (0.05 to 0.27) | 0.22 (0.11 to 0.33) |
| `walk/shortest` | 0.15 (0.04 to 0.25) | 0.23 (0.12 to 0.34) |
| Street class alone | −0.10 | 0.35 (0.26 to 0.45) |

- DVRPC counts one sidewalk, but the model puts walkers on one side of a
  street almost arbitrarily, so "whole street" (both sidewalks and the road)
  is the fairer comparison. It was chosen after both were seen.
- Counts from 2014 and earlier (129 sites) show no relationship. Later
  counts give ρ of about 0.3 to 0.55. This hasn't been explained.
- On whole streets, ranking sites by street class alone does as well as
  any preset. The modelled demand is the likeliest weak point: trips start
  only at homes, end only at job clusters (which stand in for shops and
  services too), and leave out transit riders' walks.

## Credits

- Recorded bicycle trips: CyclePhilly, Code for Philly and the Delaware
  Valley Regional Planning Commission (DVRPC).
- Walking counts: DVRPC, under the DVRPC Data License.
- Streets: © OpenStreetMap contributors (ODbL).
- Population: US Census Bureau, 2020 Census. Jobs: US Census Bureau,
  LEHD LODES 8.
- Route-choice models: Broach, Gliebe and Dill (2011); Broach (2016);
  Furth's level of traffic stress. Full citations are in each preset and
  in `docs/assumptions.md`.
