# 58: Results carry their scale, and old-scale results are never planned from

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 54

**Spec:** `.scratch/current-monitor-scale/spec.md` (Part G)
**Binding:** `docs/adr/0007-current-monitor-scale-is-2-ma-per-volt.md`, `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:**
- `src/rbl/config/characterization_history.py`: `write_result`, `_all_results`, `_newest`,
  `newest`, `newest_with_capacitance`, `_present`.
- `src/rbl/gui/load_characterization_tab.py`: the table loop that calls
  `characterization_history.newest_with_capacitance(label, condition, now)` and writes
  `"not measured"`.
- `src/rbl/services/trip_history.py`: `append_trip`.
- `src/rbl/services/calibration_writer.py`: `config_snapshot`.
- `tests/test_characterization_history.py`: the `result(...)` and `day(...)` helpers.
- `tests/conftest.py`: `_never_touch_the_real_amplifier_stores`.

## What to build

Every number computed before ADR 0007 is 5x too high, and nothing in a stored file says
which scale it used. From now on every result says, and the planner never uses a result
measured with any other scale.

1. **`write_result`** always stores `"current_monitor_ma_per_volt"`. The value is read
   from `hardware_config.CURRENT_MONITOR_MA_PER_VOLT` at call time, through the module,
   so a test can monkeypatch it. A value the caller passes in is overwritten.
2. **`_newest`** skips any record whose `current_monitor_ma_per_volt` is missing or
   differs from the value in force. This means `newest`, `newest_for_amplifier`,
   `newest_with_capacitance`, `newest_on_plates` and `newest_on_plates_c_pf` never return
   one.
3. **New public function `newest_wrong_scale(plate_position, load_condition, now) -> dict | None`.**
   It returns the newest non-aborted record for the amplifier in force at that position
   and condition whose scale is missing or different, presented through `_present`. It
   returns None if there is none.
4. **Load Characterization view.** When `newest_with_capacitance(...)` is None and
   `newest_wrong_scale(...)` is not, the cell text is exactly
   `wrong monitor scale - remeasure`, with the `theme.WARN` background.
5. **`append_trip`** writes `current_monitor_ma_per_volt` into the JSON line. It writes a
   copy, so the caller's dict is not mutated.
6. **`config_snapshot()`** includes `"CURRENT_MONITOR_MA_PER_VOLT"` alongside the
   calibration constants.

Tests may fake: the clock (`now` is an argument) and the scale (monkeypatched on
`hardware_config`). Result files are real files in the conftest temp folder.

## Guardrails

- **Trip-history tests use a temp path.** Every test that writes trip history passes an
  explicit `tmp_path` file to `append_trip(path=...)`. `TRIP_HISTORY_PATH` is bound at
  import and is not redirected by `tests/conftest.py`, so the default path is the
  operator's real file.
- **Nothing old is changed.** No rescaling, migration or rewrite of any existing file.
  Old files are left for the archive script (ticket 59).

## Acceptance criteria

- [ ] **Write.** `test_a_written_result_records_the_scale` (new,
  `tests/test_characterization_history.py`): the JSON written by `write_result` has
  `current_monitor_ma_per_volt == 2.0`, even when the caller passed `10.0`.
- [ ] **Missing scale.** `test_a_result_without_a_scale_is_never_newest` (new):
  - setup: a result file written by hand with no scale key, plus a newer
    `write_result`-written one for a different condition;
  - result: `newest(...)` for the first condition is None, and
    `newest_wrong_scale(...)` returns that record.
- [ ] **Different scale.** `test_a_result_with_a_different_scale_is_never_newest` (new):
  - setup: a record written while `hardware_config.CURRENT_MONITOR_MA_PER_VOLT` was
    monkeypatched to 10.0, then the patch restored;
  - result: `newest_on_plates_c_pf(...)` is None.
- [ ] **View.** `test_the_view_says_wrong_monitor_scale_for_an_old_result` (new,
  `tests/test_load_characterization_tab.py`): that plate and condition cell reads
  `wrong monitor scale - remeasure`.
- [ ] **Trip history.** `test_a_trip_record_carries_the_scale` (new,
  `tests/test_trip_history.py`, using `path=tmp_path / "t.jsonl"`): the loaded record has
  `current_monitor_ma_per_volt == 2.0`, and the dict passed in has no such key.
- [ ] **Calibration snapshot.** `test_the_calibration_snapshot_carries_the_scale` (new,
  `tests/test_calibration_writer.py`): `config_snapshot()["CURRENT_MONITOR_MA_PER_VOLT"]
  == 2.0`.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
