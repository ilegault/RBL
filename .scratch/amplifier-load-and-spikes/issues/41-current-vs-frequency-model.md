# 41: The current-vs-frequency chart model, with a margin verdict per plate

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/amplifier-load-and-spikes/spec.md` (Part D)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/hardware/raster_plan.py` (`envelope_status` - the new function sits beside it), `src/rbl/hardware/load_model.py` (`envelope_walls`, `_SHAPE_K`).

## What to build

A pure function `raster_plan.current_vs_frequency(caps, plate_kv, freq_of_axis, shape,
axis_of_channel=None)`. `caps` is `{plate: c_pf | None}` (`None` = not measured). Returns:

- `freq_hz`: 200 log-spaced points from 10 Hz to 10 kHz;
- `series`: `{plate: [mA, ...]}` for measured plates only, I = k x f x C x V, k from the shape;
- `operating_point`: `{plate: (freq_hz, mA)}` for measured plates;
- `levels`: `{"continuous_ma": 20.0, "burst_ma": 100.0}`;
- `verdict`: `{plate: "green" | "amber" | "red" | "not_measured"}` from the operating-point
  current: green below 10 mA, amber from 10 mA up to and including 16 mA, red above 16 mA.

Constants `MARGIN_AMBER_MA = 10.0` and `MARGIN_RED_MA = 16.0` live in `raster_plan`, commented
as 50 % and 80 % of the continuous rating.

Tests may fake: nothing. Plain numbers.

## Acceptance criteria

- [ ] C = 1600 pF, triangle, 2 kV, 500 Hz: operating-point current 6.4 mA (to 1e-9), verdict `green`.
- [ ] Operating points of exactly 10.0 and 16.0 mA are `amber`; 9.999 mA is `green`; 16.001 mA is `red`.
- [ ] A plate with `None` has no `series` or `operating_point` entry and verdict `not_measured`.
- [ ] A sine at the same C, V and f gives an operating-point current 2*pi/4 times the triangle's.
- [ ] `levels` is exactly `{'continuous_ma': 20.0, 'burst_ma': 100.0}`.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
