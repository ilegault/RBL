# 132: One poll error no longer disconnects the Galil

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 126

**Spec:** `.scratch/slit-motion/spec.md`
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/hardware/galil_workers.py` (`GalilPollWorker.run`),
`src/rbl/gui/motor_tab.py` (`MotorTab._do_connect` where the poll worker is wired,
`_on_poll_error`, `_do_disconnect`, `_cancel_all_homing`).

## What to build

Today the 5 Hz poll gives up on its first exception: it emits `error`, the tab disconnects the
Galil, and any homing run in progress is cancelled. A single slow reply during a busy homing run
is enough. After this ticket the poll tolerates isolated failures and disconnects only after
`GALIL_POLL_MAX_CONSECUTIVE_ERRORS` failures in a row.

1. `hardware_config.py`: `GALIL_POLL_MAX_CONSECUTIVE_ERRORS = 3`, with a comment giving the reason.
2. `GalilPollWorker`: add `warning = Signal(str)`. In `run`, an exception increments a consecutive
   failure count. Below the limit it emits
   `warning(f"Poll error {count}/{SC.GALIL_POLL_MAX_CONSECUTIVE_ERRORS}: {e}")` and the loop
   continues after the normal period. At the limit it emits `error(str(e))` and stops, as today. A
   successful poll resets the count to 0.
3. `MotorTab`: move the poll worker's signal wiring out of `_do_connect` into
   `_wire_poll_worker(self, worker: GalilPollWorker) -> None` (connects `state` → `_on_state`,
   `error` → `_on_poll_error`, and the new `warning` → a slot that calls
   `self._log_line("! " + msg)`), and call it from `_do_connect`. `_on_poll_error` is unchanged
   (it still disconnects).
4. Update `GalilPollWorker`'s docstring with the rule and the incident.

## Acceptance criteria

- [x] Test `test_poll_survives_isolated_errors` in `tests/test_galil_poll_worker.py` (new file): a
      `MagicMock` galil whose `get_position` raises `ConnectionError("Timeout")` on its 1st and 2nd
      calls and returns 0 afterwards, and whose `connected` stays true for 12 calls then turns false;
      `GalilPollWorker.msleep` patched to a no-op; calling `worker.run()` directly emits two
      `warning`s (`"Poll error 1/3: Timeout"`, `"Poll error 2/3: Timeout"`), no `error`, and at least
      one `state`.
- [x] Test `test_poll_gives_up_after_three_in_a_row` in `tests/test_galil_poll_worker.py`:
      `get_position` always raises `ConnectionError("Timeout")`; `run()` emits two `warning`s, then
      exactly one `error` equal to `"Timeout"`, then returns.
- [x] Test `test_a_good_poll_resets_the_error_count` in `tests/test_galil_poll_worker.py`:
      `get_position` raises on calls 1 and 2, returns 0 on calls 3–6 (one full four-axis poll),
      raises on calls 7 and 8, and returns 0 from then on; `connected` stays true for 12 checks;
      no `error` is emitted and the four `warning`s are 1/3, 2/3, 1/3, 2/3 in that order.
- [x] Test `test_poll_warning_reaches_the_console_without_disconnecting` in
      `tests/test_galil_poll_worker.py`: a real `Beamline` and `MotorTab` with
      `beamline.galil = MagicMock(connected=True)`; build a `GalilPollWorker(beamline.galil)` (not
      started), pass it to `tab._wire_poll_worker(worker)`, emit `worker.warning.emit("Poll error 1/3: Timeout")`;
      the console contains
      `"! Poll error 1/3: Timeout"` and `beamline.galil.disconnect` was not called.

**What tests may fake:** the Galil (`MagicMock`) and `GalilPollWorker.msleep`. The worker's loop,
`Beamline` and `MotorTab` are real.

**Out of scope:** reconnecting automatically. Changing the poll period.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments

2026-11-17: Implemented in PR #196. Poll tolerates 2 consecutive errors, disconnects on the 3rd; a good poll resets. wire_poll_worker is public (no underscore) because the private-access ratchet in tests/test_layering.py is at its limit (462). Gate green locally and in CI.
