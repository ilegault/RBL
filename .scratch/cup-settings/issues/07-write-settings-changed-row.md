# 07: A session-file row for every settings change

**Blocked by:** 05

**Status:** done

**Read first:** `src/rbl/services/cup_session_writer.py`, the existing `write_*`
family - in particular `write_cycle_insertion_skipped` and `write_position_transition`
- for the row shape, the timestamp columns and the use of `_safe_float`.
`docs/adr/0002-...md` decision 3 (the application's watching is recorded, so a gap
never means three different things) and amendment decision A8.

**What to build:** A marker row saying that a setting changed, when, from what, to
what. Without it, a reader comparing two insertions judged by different thresholds can
see that the numbers differ but not when or why the change happened.

- [x] `CupSessionWriter.write_settings_changed(t_host: float, key: str, old_value:
      float, new_value: float)` writes one row following the conventions of
      `write_cycle_insertion_skipped`: same timestamp handling, same event-type
      column, values formatted through `_safe_float`. A test asserts the row appears
      in the CSV with the old and new values readable.
- [x] `key` is restricted to exactly these five strings: `arm_threshold_a`,
      `release_threshold_a`, `settle_window_s`, `cycle_period_s`, `cycle_dwell_s`. Any
      other value raises `ValueError` naming the key. A test asserts one accepted key
      writes a row and that `"arm_debounce_s"` raises - the two file-only settings
      cannot change while the application runs, so a row claiming one did would be
      false.
- [x] The row is written whether or not a run is open, and does not open, close or
      otherwise disturb a run. A test writes a settings-change row in the middle of an
      open run and asserts `run_count`, `sample_count` and the run's recorded
      thresholds are unaffected, and that the run's closing row still appears.
- [x] `row_count` includes the new row, and the row survives the flush-after-every-row
      policy: a test asserts the row is readable from the file on disk **before** the
      writer is closed. A 12-hour run that dies at hour 11 must leave 11 hours of
      usable data, which is why every row is flushed.
- [x] The method's docstring states what a reader uses this row for: to see which
      thresholds applied to which insertions when the values changed mid-session.

**Tests may fake:** the output directory, via `tmp_path`. The CSV write and read-back
must be real.

**Out of scope:** every caller of this method (tickets 12, 13 and 15) and the cycle
lifecycle rows (ticket 08).

## Comments
- 2026-10-06: Added `write_settings_changed(t_host, key, old_value, new_value)` to `CupSessionWriter` writing `record_type="settings_changed"` rows with `_safe_float` formatting and immediate disk flush. Validated key restriction to the 5 editable settings (`arm_threshold_a`, `release_threshold_a`, `settle_window_s`, `cycle_period_s`, `cycle_dwell_s`), verified open run isolation and row count increment. Tested comprehensively in `TestWriteSettingsChanged` in `tests/test_cup_session_writer.py`.

