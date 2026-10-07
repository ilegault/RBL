# 37: Mode A and Mode C write characterization results tagged with the assigned amplifier

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 36

**Spec:** `.scratch/amplifier-load-and-spikes/spec.md` (Part A)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/services/load_characterizer.py` (`LoadCharacterizer.__init__`, `_finish`, `_write_csv`), `src/rbl/config/characterization_history.py` (`write_result`, from 36), `tests/test_load_characterizer.py` (`test_finished_emits_and_persists_measurement`).

## What to build

`LoadCharacterizer._finish` stops calling `load_calibration_store.save_measurement` and calls
`characterization_history.write_result` instead, with `plate_position` = the run's amp label,
`amplifier_serial` = the serial the current assignment puts there (`"unassigned"` if none),
`load_condition`, `method` (`impedance_sweep` for Mode A, `charge_integral_ladder` for Mode C),
`values` `{"c_pf", "g_us"}` and `points` = the run's per-point rows. An aborted run writes a
result with `aborted: true` and no `values["c_pf"]`. The per-run CSV (`_write_csv`) is unchanged.

`LoadCharacterizer` takes `now_fn` (default: local now, tz-aware) so tests pass time.

Rewrite `test_finished_emits_and_persists_measurement` in place, same name, to assert the new
result file instead of the old store.

Tests may fake: the function generator (as `tests/test_load_characterizer.py` already does)
and `now_fn`. The result folder is real (temp path).

## Acceptance criteria

- [x] A completed synthetic Mode A run on X+ writes exactly one file to the temp `CHARACTERIZATION_DIR` with `method` `impedance_sweep`, `plate_position` `X+`, and `values.c_pf` within the existing test's tolerance of the synthetic capacitance.
- [x] With an assignment putting `S-123` on X+, that file's `amplifier_serial` is `S-123`; with no assignment it is `unassigned`.
- [x] An aborted Mode A run writes a file with `aborted: true` and no `c_pf`, and `characterization_history.newest` does not return it.
- [x] After any run, the temp `load_calibration.json` from the conftest fixture does not exist.
- [x] All other tests in `tests/test_load_characterizer.py` pass unchanged.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments

2026-10-07: Done. `LoadCharacterizer` takes `now_fn` and writes its result through `characterization_history.write_result` (Mode A `impedance_sweep`, Mode C `charge_integral_ladder`; aborted runs written with `aborted: true` and empty `values`; Mode B writes none). `test_finished_emits_and_persists_measurement` rewritten in place; new `TestResultFiles` in `tests/test_load_characterizer.py` covers each criterion plus load condition, Mode C, Mode B, and the retired store not being written. All other tests in that file are unchanged.
Notes: a completed run with no valid capacitance (all NaN / no edges) writes nothing, as before. Mode C still reports `g_us` 0.0 as the old code did; ticket 43 replaces Mode C with the voltage ladder.
