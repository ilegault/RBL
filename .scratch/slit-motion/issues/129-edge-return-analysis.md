# 129: Edge-return analysis

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/slit-motion/spec.md`
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `CONTEXT.md` (**Slit motion**: *lost step*, *edge-return test*),
`src/rbl/hardware/profile_fwhm.py` or `src/rbl/hardware/edge_metrics.py` (the shape of a pure
`hardware/` module: module docstring with WHY THIS EXISTS, frozen dataclass result, plain inputs),
`src/rbl/config/hardware_config.py` (`STEP_RESOLUTION`, `STEPS_PER_MM`).

## What to build

The operator sees the home switch "somewhere else" when coming back to home and cannot tell
whether that is switch jitter or lost steps. The edge-return test (tickets 130–131) collects the
commanded position at the home switch's transition, cycle after cycle, without redefining zero.
This ticket is the pure math that turns those readings into an answer. No Galil, no Qt.

1. `hardware_config.py`: `LOST_STEP_GROUP_COUNTS = 4 * STEP_RESOLUTION` (a stalled two-phase
   stepper slips by whole electrical cycles, four full steps; at half-stepping that is 8 counts)
   and `LOST_STEP_TOLERANCE_COUNTS = 2`, each with a one-line comment saying that.
2. New module `src/rbl/hardware/edge_return.py` with a WHY THIS EXISTS docstring, containing:
   - `is_lost_step_jump(delta_counts: int) -> bool`: with `g = SC.LOST_STEP_GROUP_COUNTS`,
     `tol = SC.LOST_STEP_TOLERANCE_COUNTS`, `m = abs(delta_counts)`,
     `nearest = round(m / g) * g`; returns `nearest >= g and abs(m - nearest) <= tol`.
   - frozen dataclass `EdgeReturnSummary` with fields `n: int`, `deltas_counts: tuple[int, ...]`
     (consecutive differences, length `n - 1`), `total_drift_counts: int` (last minus first),
     `spread_counts: int` (max minus min), `std_counts: float` (population standard deviation of
     the readings), `lost_step_jumps: tuple[int, ...]` (indexes into `deltas_counts` where
     `is_lost_step_jump` is true).
   - `analyse_edge_returns(edge_counts: list[int]) -> EdgeReturnSummary`; raises `ValueError` for
     fewer than two readings.
   - `summary_line(summary: EdgeReturnSummary, axis_letter: str, pass_speed_cps: int) -> str`
     returning exactly
     `f"{axis_letter} [{slit}] @ {pass_speed_cps} cps: n={n}, spread {spread} cts ({spread_um:.1f} µm), drift {total:+d} cts, σ {std:.2f} cts, lost-step jumps: {k}"`
     where `slit = SC.AXIS_NAMES[axis_letter]`, `spread_um = spread / SC.STEPS_PER_MM[axis_letter] * 1000`,
     `k = len(lost_step_jumps)`.

## Acceptance criteria

- [ ] Test `test_lost_step_jump_rule` in `tests/test_edge_return.py` (new file): `is_lost_step_jump`
      is `True` for 8, -8, 6, 10, 16, -15 and `False` for 0, 1, 3, 5, 12, -4.
- [ ] Test `test_analysis_of_a_steady_edge` in `tests/test_edge_return.py`: readings
      `[1000, 1001, 999, 1000, 1001]` give `n == 5`, `deltas_counts == (1, -2, 1, 1)`,
      `total_drift_counts == 1`, `spread_counts == 2`, `lost_step_jumps == ()`, and
      `std_counts == pytest.approx(0.748, abs=1e-3)`.
- [ ] Test `test_analysis_finds_a_lost_step_jump` in `tests/test_edge_return.py`: readings
      `[1000, 1000, 1008, 1008, 1009]` give `lost_step_jumps == (1,)` and `total_drift_counts == 9`.
- [ ] Test `test_summary_line_text` in `tests/test_edge_return.py`: for readings
      `[1000, 1001, 999, 1000, 1001]`, axis `"A"`, speed 58, the line equals
      `"A [X+] @ 58 cps: n=5, spread 2 cts (3.2 µm), drift +1 cts, σ 0.75 cts, lost-step jumps: 0"`.
- [ ] Test `test_analysis_needs_two_readings` in `tests/test_edge_return.py`: `[]` and `[5]` each raise
      `ValueError`.

**What tests may fake:** nothing. This is pure math on plain integers.

**Out of scope:** talking to the Galil (ticket 130), the tab and the CSV (ticket 131).

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
