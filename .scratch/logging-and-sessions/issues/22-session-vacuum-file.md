# 22: Each session writes its own 1 Hz vacuum.csv

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 19

**Spec:** `.scratch/logging-and-sessions/spec.md` (section B1)
**Binding:** `docs/adr/0004-monitoring-log-and-sessions.md` (decisions 4, 6), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/services/session_recorder.py` (`start`, `stop`, `_write_manifest`),
`src/rbl/gui/app.py` (`MainWindow.__init__`, where `self.session_recorder` is built
and where `beamline` signals are connected), `src/rbl/gui/vacuum_tab.py`
(`_build_comment_lines`), `tests/test_session_recorder.py` (`_make_recorder`).

## What to build

A session folder holds the experiment's full vacuum record. `SessionRecorder` gains a
public slot `on_vacuum_state(state) -> None`. While recording it writes through
`VacuumLogger(labels, output_dir=<session folder>, file_stem="vacuum")`, opened on the
first state after `start()`. A gauge-set change closes it and opens the next
(`vacuum_2.csv`). `stop()` closes it. While not recording the slot does nothing.
`session.json` gains `"vacuum": {"files": [<basenames>]}`.

Move the header rule from `VacuumTab._build_comment_lines` into a pure
`vacuum_comment_lines(state, utc_now: datetime) -> list[str]` in `vacuum_logger.py`; the
tab's static method calls it, so both files have the same header.

In `MainWindow.__init__`, connect `self.beamline.vacuum_changed` to
`self.session_recorder.on_vacuum_state` with a one-line comment citing ADR 0004
decision 6.

Tests may fake: the camera (as `_make_recorder` does) and the vacuum states (copy the
`_Fake*` stand-ins from `tests/test_vacuum_logger.py`). Must be real: the
`SessionRecorder` and the files it writes under `tmp_path` (point `LOGS_DIR` there as the
existing recorder tests do).

## Acceptance criteria

- [x] Start a session, deliver 5 states to `on_vacuum_state`, stop: the session folder
      contains `vacuum.csv` with the `#` header and 5 data rows, and `vacuum.json`.
- [x] States delivered before `start()` and after `stop()` write nothing anywhere.
- [x] A state with a different gauge set mid-session produces `vacuum_2.csv`, and
      `session.json` `vacuum.files` lists both.
- [x] `vacuum_comment_lines` is used by both `VacuumTab._build_comment_lines` and the
      recorder: a test asserts the session file's first header line starts with
      `# vacuum_logger RBL`.
- [x] Every existing test in `tests/test_session_recorder.py` passes unchanged.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments

### Landed 2026-10-05
- Added `on_vacuum_state` slot to `SessionRecorder` in `src/rbl/services/session_recorder.py` writing 1 Hz vacuum data to session folder, handling gauge-set rollover, closing on session stop, and recording vacuum file basenames in `session.json`.
- Extracted pure `vacuum_comment_lines` and `gauge_labels` to `src/rbl/services/vacuum_logger.py`, delegated `VacuumTab._build_comment_lines` to `vacuum_comment_lines`.
- Connected `beamline.vacuum_changed` to `session_recorder.on_vacuum_state` in `MainWindow.__init__` citing ADR 0004 decision 6.
- Added comprehensive unit tests in `tests/test_session_recorder.py` covering all acceptance criteria and app wiring.
