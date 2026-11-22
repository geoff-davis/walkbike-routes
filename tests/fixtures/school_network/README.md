# Synthetic school-network CI fixture

`mini_district.json` is a small, deterministic, metric-CRS dataset used to run core modeling
paths on a clean checkout. It is deliberately synthetic: its coordinates, population, streets,
crossings, controls, and collisions are not claims about Los Altos.

The fixture uses destination-neutral `destinations`, `access_points`, `generic_origins`, and
`origin_attachments` tables. Legacy school/car aliases remain temporarily so the fixture can
characterize existing entry points during the accessibility-core migration.

The fixture covers:

- raw street attributes normalized by the production LTS and pedestrian-evidence rules;
- a directional bicycle-facility gap and a low-stress parallel route;
- north/south Maple walking topology, an explicit crossing, and a non-Maple marking;
- equal-residential and capacity-weighted Census-block disaggregation, including fallback;
- a shortest car route that loses to a bypass after turn and signal delay; and
- overlapping collision points, party classification, half-mile selection, and display jitter.

Regenerate the committed JSON after intentionally editing the fixture specification:

```bash
uv run python tests/fixtures/school_network/generate.py
uv run pytest -q tests/test_ci_school_network_fixture.py
```

CI runs the generator in check mode. Keep the expected outcomes small and explain any intentional
change in the commit; do not update them merely to make a regression pass.
