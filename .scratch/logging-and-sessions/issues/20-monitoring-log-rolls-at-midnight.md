# 20: The vacuum monitoring log rolls at local midnight

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 19

**Spec:** `.scratch/logging-and-sessions/spec.md` (section A3)
**Binding:** `docs/adr/0004-monitoring-log-and-sessions.md` (decisions 1-3, 8), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/services/vacuum_logger.py` (`VacuumLogger.write_row` returns
`False` on a gauge-set change), `src/rbl/gui/vacuum_tab.py` (`_on_vacuum_state`, the
reopen block, and `_build_gauge_labels` / `_build_comment_lines`), and
`src/rbl/services/log_rollover.py` from ticket 19.

## What to build

The rule for the continuous vacuum log, as a class with no Qt that can be driven with
chosen times: one file per local day, month folders, a gauge change opens a new file, and
a stopped log stays stopped through midnight.

New `src/rbl/services/vacuum_monitor_log.py`, `class VacuumMonitorLog` with
`__init__(self, root: Path | None = None, *, comment_lines_fn=None)` (root defaults to
`VACUUM_DIR`), `write(state, now_local: datetime)`, `start()`, `stop() -> str | None`,
`is_running` (True on construction), `current_path`. `write` rules in order: not running,
return; no file, open at `month_folder(root, now_local)` with
`timestamped_stem("vacuum", now_local)`, write header from `comment_lines_fn(state)`,
then the row; `crossed_local_midnight(opened, now_local)`, close, open the next file,
header, then this row (the row goes to the new file only); `write_row` returns `False`,
close, open a new file at `now_local`, header, row. Gauge labels are built the same way
as `VacuumTab._build_gauge_labels`; move that rule into a module-level function
`gauge_labels(state) -> list[str]` in `vacuum_logger.py` and have the tab's static method
call it.

Tests may fake: the `VacuumState` (use the `_Fake*` stand-ins in
`tests/test_vacuum_logger.py`) and the times. Must be real: files written under
`tmp_path` and read back with `csv`.

## Acceptance criteria

- [ ] New `tests/test_vacuum_monitor_log.py`: 10 states at 23:59:50-23:59:59 and 10 at
      00:00:00-00:00:09 the next day produce exactly two CSVs, one in each correct
      `YYYY-MM` folder. Read back, the total data-row count is 20, the second file's first
      data row has the `unix_time` of the 00:00:00 state, and each file begins with the
      `#` header lines.
- [ ] A crossing from the last day of a month to the first creates the second month's
      folder and puts the post-midnight rows there.
- [ ] After `stop()`, 5 more writes, including one after midnight, create no new file and
      add no row to the closed one. `stop()` returns the closed file's path.
- [ ] After `stop()` then `start()`, the next write opens a new file named from that
      write's time.
- [ ] A state with a different gauge set at the same time of day closes the file and
      opens a second one in the same folder (a `_2` suffix if the second has the same
      stem), with its own header.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
