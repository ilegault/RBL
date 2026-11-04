# 82: Poll the picoammeter back-to-back while a run is open

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/cup-target-uncertainty/spec.md`
**Binding:** `docs/adr/0003-commanded-and-confirmed-cup-position.md` (amendment D, and C1), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/hardware/picoammeter_worker.py` (module docstring, `set_acquiring`, `set_poll_interval`, `run`); `src/rbl/config/cup_config.py` "Polling rates".

## What to build

Today the worker sleeps 0.1 s *after* every `:READ?`, so an insertion gets fewer than
10 samples a second. While a run is open it should read again as soon as the previous
read returns, so the instrument sets the pace. Idle polling stays at 2 Hz.

1. `src/rbl/config/cup_config.py`: replace `CUP_POLL_RATE_ACQUIRING_HZ` and
   `CUP_POLL_INTERVAL_ACQUIRING_S` with `CUP_POLL_INTERVAL_ACQUIRING_S: float = 0.0`
   and a comment: 0 means back-to-back; the achieved rate is set by the read time
   (NPLC 1 integration plus GPIB round trip) and is measured by the bench ticket in this
   set. Delete `CUP_POLL_RATE_ACQUIRING_HZ` (grep shows no other user).
2. `PicoammeterWorker.set_poll_interval`: clamp with `max(0.0, ...)` instead of
   `max(0.01, ...)`.
3. `PicoammeterWorker.run`: when the interval is 0, skip `_sleep_interruptible`
   entirely; the loop still checks `is_running()` every iteration so `stop()` stays
   responsive.
4. Update the module docstring "POLLING RATES" section to say back-to-back.

## Acceptance criteria

- [x] `tests/test_picoammeter_worker.py::test_set_poll_interval_and_acquiring` (rewrite
      in place, same name) asserts `set_acquiring(True)` gives an interval of `0.0` and
      `set_acquiring(False)` gives `CUP_POLL_INTERVAL_IDLE_S`.
- [x] A new test runs the worker with the Keithley driver mocked (as
      `test_worker_run_and_stop_lifecycle` does), acquiring, with
      `_sleep_interruptible` patched to record its calls, and asserts that after several
      reads it was never called with a positive duration.
- [x] A new test asserts that when not acquiring, `_sleep_interruptible` is called with
      `CUP_POLL_INTERVAL_IDLE_S` between reads.
- [x] `stop()` on an acquiring worker returns within the existing lifecycle test's
      timeout.

May fake: the Keithley driver class and `_sleep_interruptible`. Must be real: the worker's
own `run` loop and interval logic.

## Gate

Run all four, in CI's order (`.github/workflows/tests.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
