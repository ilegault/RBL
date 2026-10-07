# 51: A drift pass on the plates: no time cap, protections required, observe-only

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 40, 47

**Spec:** `.scratch/amplifier-load-and-spikes/spec.md` (Part G)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `docs/adr/0005-drift-pass-on-plates-guarded-by-protections-not-a-clock.md`, `docs/adr/0006-amplifier-faults-are-observed-not-stopped-during-a-session.md`

**Read first:** `src/rbl/services/calibration_runner.py` (`start_drift`, `_check_overcurrent`, `on_window`), `src/rbl/gui/calibration_tab.py` (the drift-duration refusal near `rb_drift` in the run handler), `tests/test_calibration_runner.py` (`TestDriftLoadConditionGuard`: `test_on_plates_8h_refused`, `test_on_plates_1h_accepted`).

## What to build

- `start_drift` gains `protections_ok: callable() -> (bool, str)`. For ON_PLATES there is no
  duration cap; the pass starts only if `protections_ok()` returns `(True, ...)`, re-checks on
  every window, and on `(False, reason)` ends and emits `error` with the reason (for example
  `HV interlock not permitting 2.0 kV` or `spike recorder not running`). DISCONNECTED keeps its
  12 h cap.
- During an ON_PLATES drift pass, `_check_overcurrent` does not end the run (ADR 0006);
  over-current windows are still logged.
- `calibration_tab.py` removes its own ON_PLATES duration refusal, supplies `protections_ok`
  from the HV interlock state and `SpikeRecorder.is_running()`, and starts the spike recorder
  into the drift pass's output folder.
- Rewrite in place, same names: `test_on_plates_8h_refused` asserts an 8 h ON_PLATES pass is
  refused when `protections_ok` returns False and that the message names the protection;
  `test_on_plates_1h_accepted` asserts acceptance with protections OK.

Tests may fake: the function generator, the payloads and `protections_ok`.

## Acceptance criteria

- [ ] A 24 h ON_PLATES drift with `protections_ok` returning `(True, '')` starts (state leaves IDLE).
- [ ] With `(False, 'spike recorder not running')` the pass does not start and `error` carries that text.
- [ ] Flipping `protections_ok` to False mid-run ends the pass within one window and zeroes the outputs; the message names the reason.
- [ ] A synthetic 40 mA window during an ON_PLATES drift does not end the run; the existing over-current tests for sweeps pass unchanged.
- [ ] `test_disconnected_10h_accepted` and `test_disconnected_20h_refused` pass unchanged.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
