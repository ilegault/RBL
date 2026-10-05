# 32: Sessions open the cup log and offer to continue the previous dose

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 24, 31

**Spec:** `.scratch/logging-and-sessions/spec.md` (section C6)
**Binding:** `docs/adr/0003-commanded-and-confirmed-cup-position.md` (2026-10-05 amendment, C5), `docs/adr/0002-cup-acquisition-triggered-by-current.md` (2026-10-05 amendment, B1), `docs/adr/0004-monitoring-log-and-sessions.md` (decision 4), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/services/session_recorder.py` (`start`, `stop`, the disk
`auto_stop` call in `_on_segment_closed`, `session_started` / `session_stopped`),
`src/rbl/gui/app.py` (`MainWindow.__init__`), `src/rbl/services/cup_log.py`
(`find_previous_session_cup_log`, `read_dose_totals`, `ContinueChoice`),
`tests/test_e2e_session.py` for how a `MainWindow` is built in tests.

## What to build

A session is the irradiation: it opens the cup log, and it can carry the dose from the
previous session (over a weekend, say), always asking, with no time limit.

- `SessionRecorder` gains `set_start_guard(fn)` and `set_stop_guard(fn)`, each a
  `Callable[[], bool]`. `start()` and `stop()` call their guard first and do nothing when it
  returns `False`. The disk `auto_stop` path calls `stop(force=True)`, which skips the guard.
- `MainWindow` installs a start guard: if automatic insertion runs in a test cup log, run
  the tab's confirmed stop; declining returns `False`. And a stop guard: if automatic
  insertion runs, the tab's confirmed stop; declining returns `False`.
- On `session_started(folder)`: `previous = find_previous_session_cup_log(LOGS_DIR,
  exclude=folder)`; if found, `self._ask_continue_dose(totals) -> ContinueChoice | None`
  (a replaceable method wrapping the dialog in spec C6; `None` means start from zero).
  Then `cup_log.open_for_session(folder, continuation=...)`. When continuing, the tab
  seeds its accumulator with `seed(totals)` and, for a no-beam gap,
  `exclude_interval(totals.last_out_t, now)`.
- On `session_stopped`: `cup_log.close()`.

Tests may fake: the camera, instrument payloads, and `_ask_continue_dose` and
`_confirm_stop_automatic` (monkeypatched on the instance). `LOGS_DIR` and the cup test
root point at `tmp_path`. Session folders and cup files must be real.

## Acceptance criteria

- [ ] Starting a session creates `<session folder>/cup.csv` and `CupLog.kind` is
      `SESSION`. With a test cup log open beforehand, that file is closed and untouched
      afterwards.
- [ ] With an earlier session folder whose `cup.csv` has counted rows (written by a real
      writer), `_ask_continue_dose` is called once with those totals. Choosing continue
      writes the `# dose continued from:` header, and the tab's charge label starts at the
      earlier total. Choosing not to continue starts at zero.
- [ ] With no earlier counted session, `_ask_continue_dose` is not called.
- [ ] Stopping a session while automatic insertion runs, with `_confirm_stop_automatic`
      returning `False`, leaves the session recording and the cup log open. Returning
      `True`, the session stops and the cup log closes.
- [ ] Disk auto-stop stops the session without calling either guard.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
