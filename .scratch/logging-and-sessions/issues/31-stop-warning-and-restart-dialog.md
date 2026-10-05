# 31: Stop asks first, restart asks about the gap, and Start/Stop are two buttons

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 30

**Spec:** `.scratch/logging-and-sessions/spec.md` (section C5)
**Binding:** `docs/adr/0003-commanded-and-confirmed-cup-position.md` (decision 3; 2026-10-05 amendment, C3, C4), `docs/adr/0002-cup-acquisition-triggered-by-current.md` (amendment A6, A8), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/faraday_cup_tab.py`: `_on_cycle_arm_clicked` (the button
doubles as Disarm and leaves the cup where it is), `_on_cycle_stop_clicked` (withdraws),
`_update_cycle_view`, the `chk_keep_schedule` checkbox and `resume_at` handling that
cup-settings ticket 16 added, and the fault paths in `on_cup_actuation_state` that disarm
on an unconfirmed move or stale FIO state. `src/rbl/services/sampling_cycle.py` (`arm`
return value, `stop`, `saved_boundary_t`). `RestartChoice` in `cup_log.py`.

## What to build

- Two buttons: `btn_cycle_arm` becomes start-only; Stop (`_on_cycle_stop_clicked`) is the
  only way an operator stops, and it always withdraws. The disarm-in-place branch of
  `_on_cycle_arm_clicked` is removed. (Label text is ticket 33; keep current wording.)
- Operator Stop first calls `self._confirm_stop_automatic() -> bool`, a method wrapping a
  `QMessageBox` with the text in spec C5 and buttons `Stop automatic insertion` /
  `Keep running`. `False` changes nothing. Fault stops do not call it. The stop time is
  kept as the stop-gap start.
- Start, when this cup log already has a stop gap, calls `self._ask_restart(gap_start_t,
  now_t, saved_boundary_t) -> RestartChoice | None`, a method wrapping a dialog that asks
  the beam question with no default and offers `Pick up where you left off` (enabled only
  while `saved_boundary_t` is in the future) or `Start a new schedule now`. `None` starts
  nothing. `beam_on_during_gap=False` calls `exclude_interval(gap_start_t, now_t)`. The
  choice passes `resume_at` as ticket 16 does, and
  `write_automatic_insertion_restarted(...)` records gap, answer and the scheduler's
  returned mode. The first start in a cup log asks nothing.
- `chk_keep_schedule` is removed; the dialog replaces it.

Tests may fake: the two dialog methods (monkeypatch `_confirm_stop_automatic` and
`_ask_restart` on the instance) and instrument payloads. Everything else is real,
including the file. Tests that ticket 16 wrote against `chk_keep_schedule` are rewritten
in place under the same names to drive `_ask_restart` instead. No test function is deleted.

## Acceptance criteria

- [ ] `_confirm_stop_automatic` returning `False`: the scheduler stays armed, the cup is
      not commanded, and no row is written. Returning `True`: the cup is commanded out and
      a disarm row is written.
- [ ] A fault stop (unconfirmed move timeout) stops automatic insertion without calling
      `_confirm_stop_automatic` (the patched method records zero calls).
- [ ] Restart with `RestartChoice(resume_schedule=True, beam_on_during_gap=False)` before
      the saved boundary: the next insertion lands on the saved boundary, the next counted
      row's `beam_on_seconds` excludes the gap, and the restart row says `resumed` and
      `beam_on_during_gap=false`.
- [ ] Restart with `beam_on_during_gap=True` and `resume_schedule=False`: the gap is held
      (not excluded), and the row says `fresh`. `_ask_restart` returning `None`: nothing
      starts and nothing is written.
- [ ] `grep -n "chk_keep_schedule" src/` returns nothing, and the first start of a new cup
      log calls `_ask_restart` zero times.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
