# 171: Beamline can release and re-close the cup interlock, and reports which

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 168

**Spec:** `.scratch/cup-status-fix/spec.md` (D7)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `docs/adr/0003-commanded-and-confirmed-cup-position.md`
**Glossary:** `CONTEXT.md` — **Interlock**
**Read first:** `command_cup`, `_release_cup_drive`, `_on_labjack_connect_cup`, `_on_labjack_disconnect_cup` and `_publish_cup_actuation_snapshot` in `src/rbl/state/cup_actuation_link.py`; `CupActuationState` in `src/rbl/snapshots.py`; test_relays_on_connect_and_disconnect in `tests/test_cup_actuation.py` as the model (a `MagicMock` `lj` with `connected = True`, written lines read from `write_digital.call_args_list`).

## What to build

The interlock (relay 1 on FIO0, across P1 5–13) must be closed for the cup to go OUT by any
route, front panel included. The application closes it on T7 connect and on every command,
and opens it on disconnect and shutdown, but nothing reports which state it last commanded
and the operator cannot change it. Blocked by 168 only because both edit
`CupActuationState`, `cup_actuation_link.py` and `tests/test_cup_actuation.py`.

- `CupActuationState` gains `interlock_closed: bool = False`, documented in its Fields list:
  the interlock state the application last commanded (there is no feedback contact for it).
- `CupActuationLinkMixin` tracks `_cup_interlock_closed`: `True` after
  `_on_labjack_connect_cup` and after every `command_cup`; `False` after
  `_on_labjack_disconnect_cup` and after the new `release_cup_interlock`.
  `_publish_cup_actuation_snapshot` passes it on.
- New `release_cup_interlock()`: calls `_release_cup_drive()` (both lines to 0), sets the
  commanded position to `CupPosition.IN`, sets `_cup_interlock_closed = False`, publishes.
- New `close_cup_interlock()`: writes only `CUP_INTERLOCK_LINE` = 1 (the command line is
  left as it is), sets `_cup_interlock_closed = True`, publishes.
- When and how connect, disconnect, command and shutdown drive the two lines does not change.

## Acceptance criteria

- [ ] Test `test_release_interlock_opens_both_relays`, in `tests/test_cup_actuation.py`: after
      `_on_labjack_connect_cup()` and a reset of the mock, `release_cup_interlock()` writes
      `(CUP_INTERLOCK_LINE, 0)` and `(CUP_COMMAND_OUT_LINE, 0)`, and the last snapshot from
      `cup_actuation_changed` has `interlock_closed is False` and `commanded == CupPosition.IN`.
- [ ] Test `test_close_interlock_writes_only_the_interlock_line`, in
      `tests/test_cup_actuation.py`: after a release and a reset of the mock,
      `close_cup_interlock()` makes exactly one `write_digital` call,
      `(CUP_INTERLOCK_LINE, 1)`, and the last snapshot has `interlock_closed is True`.
- [ ] Test `test_interlock_state_follows_connect_command_and_disconnect`, in
      `tests/test_cup_actuation.py`: the last snapshot's `interlock_closed` is `True` after
      `_on_labjack_connect_cup()`, `False` after `release_cup_interlock()`, `True` after
      `command_cup_out()`, and `False` after `_on_labjack_disconnect_cup()`.
- [ ] test_relays_on_connect_and_disconnect and
      test_shutdown_leaves_both_outputs_deasserted_no_positional_command still pass unchanged. (no test: CI's full suite)

**What tests may fake:** the LabJack handle (`beamline.lj` as a `MagicMock`), as the model
test does. `Beamline` and the mixin are real.

**Out of scope:** the tab's control (ticket 172); any feedback contact for the interlock.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_cup_actuation.py
```

Run pytest on the touched test files only (implement-ticket-parallel, section 4). CI runs the full suite on the PR.

## Comments
