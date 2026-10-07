# 43: Mode C steps from 0.5 to 5 kV and reports C and the edge spike at every rung

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 37

**Spec:** `.scratch/amplifier-load-and-spikes/spec.md` (Part B)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/services/load_characterizer.py` (`start_mode_c`, `_finish_mode_c_point`, `_advance`, `_finish`), `src/rbl/hardware/load_model.py` (`capacitance_from_charge`), `tests/test_load_characterizer.py` (`test_finds_edges_and_recovers_capacitance_ballpark` - copy its synthetic square-wave builder).

## What to build

- `MODE_C_LADDER_KV = [0.5, 1.0, 2.0, 3.0, 4.0, 5.0]`; `start_mode_c(amp_label, ladder_kv=None)`
  builds one `_Step` per rung at `MODE_C_FREQ_HZ`.
- `_finish_mode_c_point` adds per rung: `rung_kv`, `measured_swing_kv` (mean per-edge delta V),
  `edge_peak_ma` (mean per-edge peak above baseline), `edge_duration_us` (mean time an edge
  stays above half its peak), `edge_charge_uc`, `inter_edge_leak_ua` (mean |current| between
  edges, in uA), and `edge_peak_is_lower_bound = edge_duration_us < 100`. Existing keys stay.
- On completion the result written through 37 has `method` `charge_integral_ladder`,
  `values.c_pf` = mean of the rungs' `c_pf_mean`, and `points` = the rungs.

Abort rules are ticket 44, not this one.

Tests may fake: the function generator and stream windows (synthetic, as the existing Mode C
test does).

## Acceptance criteria

- [ ] A synthetic 1200 pF load across all six rungs: six `point_measured` emissions with `rung_kv` 0.5, 1, 2, 3, 4, 5, each `c_pf_mean` within the existing test's tolerance of 1200.
- [ ] The written result's `values.c_pf` equals the mean of the six rung values to 1e-9.
- [ ] A synthetic edge 20 us wide reports `edge_peak_is_lower_bound is True`; one 300 us wide reports `False`.
- [ ] Each rung's `edge_charge_uc` equals C x 2 x rung_kv within 5 %.
- [ ] `test_finds_edges_and_recovers_capacitance_ballpark` and `test_no_edges_reports_nan_not_a_crash` pass unchanged.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
