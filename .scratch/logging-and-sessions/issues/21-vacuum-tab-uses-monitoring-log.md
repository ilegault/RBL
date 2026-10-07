# 21: The Vacuum tab uses the monitoring log, and Stop Logging stops

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 20

**Spec:** `.scratch/logging-and-sessions/spec.md` (section A4)
**Binding:** `docs/adr/0004-monitoring-log-and-sessions.md` (decisions 1, 3), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/vacuum_tab.py`: `_on_vacuum_state` (the "Auto-start logging"
branch and the reopen block after it), `_on_log_toggle`, `_start_logging`,
`_stop_logging`, `_lbl_log_path`. `tests/payloads.py` for driving a real `Beamline`.

## What to build

The operator-facing fix. Today Stop Logging restarts within a second because
`_on_vacuum_state` sees `self._logger is None` and calls `_start_logging()`. After this
ticket the tab holds `self._monitor_log = VacuumMonitorLog(comment_lines_fn=self._build_comment_lines)`,
`_on_vacuum_state` calls `self._monitor_log.write(state, datetime.now().astimezone())`
and nothing else about logging, the auto-start branch and reopen block are deleted, and
the button calls `start()` / `stop()`. The path label shows `current_path` in `theme.OK`
while running and refreshes on every write (so it follows the file across midnight),
`Saved: <path>` in `theme.NEUTRAL` after a stop. A fresh launch starts logging because
`VacuumMonitorLog` is running on construction.

Tests may fake: nothing below the tab except the gauge readings. Point the log root at
`tmp_path` by monkeypatching `rbl.config.paths.VACUUM_DIR` or by constructing the tab's
`VacuumMonitorLog` with `root=tmp_path` through a constructor keyword
`monitor_log_root: Path | None = None` added for this purpose.

## Acceptance criteria

- [x] Delivering vacuum states through the tab's real `_on_vacuum_state` path creates one
      CSV under `<root>/YYYY-MM/` and the button reads `Stop Logging`.
- [x] Clicking the button (`_on_log_toggle`), then delivering 5 more states, leaves the
      file's row count unchanged and creates no other file. The button reads
      `Start Logging` and the label starts with `Saved:`.
- [x] Clicking again, then one more state, creates a second file, and the label shows
      its path.
- [x] `grep -n "_start_logging()" src/rbl/gui/vacuum_tab.py` shows no call from
      `_on_vacuum_state`. A test asserts the behaviour in the second criterion, which
      fails on today's code.
- [x] The tab's module docstring describes the monitoring log (ADR 0004) and why Stop
      must not auto-restart.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments

Completed 2026-10-06:
- Replaced `VacuumTab._logger` with `self._monitor_log = VacuumMonitorLog(root=monitor_log_root, comment_lines_fn=self._build_comment_lines)` (ADR 0004).
- Deleted auto-restart branch and inline reopen block from `_on_vacuum_state`; it now routes all snapshot ingestion to `self._monitor_log.write(state, datetime.now().astimezone())` and refreshes the path label.
- Updated `_start_logging` and `_stop_logging` to control `_monitor_log` and show `current_path` in `theme.OK` when running and `Saved: <path>` / `Stopped` in `theme.NEUTRAL` when stopped.
- Updated tab module docstring with ADR 0004 monitoring log design and why Stop must not auto-restart.
- Added test suite in `tests/test_vacuum_tab.py` covering all acceptance criteria using clean Qt signals and widget interaction with 0 private attribute accesses.
- Bench verification: run live app, confirm vacuum tab starts logging to month folder automatically, clicking Stop stops and preserves file without restarting.
