# 36: Characterization results kept forever, and the queries that read them

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 35

**Spec:** `.scratch/amplifier-load-and-spikes/spec.md` (Part A)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `CONTEXT.md` (Characterization result, Load condition, Hardware change)

**Read first:** `src/rbl/config/load_calibration_store.py` (`save_measurement`, `measurement_for` - the shape being replaced), `src/rbl/services/log_rollover.py` (`unused_path` - use it so no result file is ever overwritten), `src/rbl/config/amplifier_assignments.py` (from 35).

## What to build

A new pure module `src/rbl/config/characterization_history.py`. No Qt, no clock.

- `write_result(result: dict, when: datetime) -> Path` writes one JSON file into
  `paths.CHARACTERIZATION_DIR`, stem `<plate>_<method>_<YYYYMMDDTHHMMSS>` through
  `log_rollover.unused_path`. Required keys: `plate_position`, `amplifier_serial`,
  `load_condition`, `method` (`impedance_sweep` | `charge_integral_ladder` | `clamp_test`),
  `values` (dict); optional `points` (list), `aborted` (bool). It also stores `when` and
  `amplifier_assignments.assignment_at(when)`.
- `newest(plate_position, load_condition, now) -> dict | None`: the newest non-aborted result
  for that position and condition whose `amplifier_serial` is the one currently assigned
  there, plus `age_s` (now minus its time) and `predates_hardware_change` (True when older
  than `latest_hardware_change(now)`).
- `newest_for_amplifier(serial, load_condition, now)`: the same, by amplifier regardless of
  position.
- `newest_on_plates_c_pf(plate_position, now) -> float | None`: `values["c_pf"]` of the newest
  `ON_PLATES` result, or `None`.

If no assignment exists at all, results with `amplifier_serial == "unassigned"` count as the
current amplifier's (so the tool is usable before serials are entered).

Tests may fake: nothing. Real files in the temp folder from 34.

## Acceptance criteria

- [ ] Two X+ on-plates results at day 1 and day 3: `newest('X+', 'ON_PLATES', day 4)` returns the day-3 one with `age_s == 86400`.
- [ ] Swap: an X+ result by amplifier A at day 1, a swap putting B on X+ at day 2: `newest('X+', 'ON_PLATES', day 3)` is `None`, and `newest_for_amplifier(A, 'ON_PLATES', day 3)` returns the day-1 result.
- [ ] A hardware change at day 2: the day-1 result has `predates_hardware_change is True`; a day-3 result has `False`.
- [ ] Two results written in the same second produce two files (the second ends `_2.json`).
- [ ] An `aborted: true` result is never returned by `newest`, and `newest_on_plates_c_pf` ignores `CABLE_ONLY` and `DISCONNECTED` results.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
