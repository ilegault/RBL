# 51: A drift pass on the plates: no time cap, protections required, observe-only

**Status:** done

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

- [x] A 24 h ON_PLATES drift with `protections_ok` returning `(True, '')` starts (state leaves IDLE).
- [x] With `(False, 'spike recorder not running')` the pass does not start and `error` carries that text.
- [x] Flipping `protections_ok` to False mid-run ends the pass within one window and zeroes the outputs; the message names the reason.
- [x] A synthetic 40 mA window during an ON_PLATES drift does not end the run; the existing over-current tests for sweeps pass unchanged.
- [x] `test_disconnected_10h_accepted` and `test_disconnected_20h_refused` pass unchanged.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments

2026-10-07: Done. `CalibrationRunner.start_drift(..., protections_ok=None)`: an ON_PLATES pass has no time cap (`DRIFT_MAX_ATTENDED_H` is removed), starts only if `protections_ok()` is `(True, ...)`, re-checks it on every window and, if it goes False, zeroes every output, emits `finished` and then `error` naming the reason. A missing or raising check counts as not ok. DISCONNECTED keeps its 12 h cap and needs no check. Over-current windows during an ON_PLATES pass are logged (throttled to once per 10 s per channel) and never end it. The Calibration tab drops its own refusal, supplies `protections_ok` (HV interlock permits the highest commanded kV via `Beamline.hv_interlock_status_for`, and the spike recorder is running), starts the recorder into `<run id>_spikes` beside the pass's CSV, stops it when the pass ends, and shows the reason a pass ended. `MainWindow` owns one `SpikeRecorder` fed from `raw_window_ready`.
Findings and choices:
- `_check_overcurrent` already did nothing during a drift pass (`_current_driven` is None there), so "does not end the run" held before; the new part is logging and the test for it.
- The pass drives its amplifiers through the calibration runner, not through `FuncGenSetpoints`, so the recorder would have seen every plate as "output off". `SpikeRecorder.start(output_dir, driven_plates=...)` lets the pass name the plates it drives (all four).
- A pass whose protections fail to start is refused by the tab before any hardware is commanded, and the recorder it just started is stopped.
- `test_on_plates_8h_refused` and `test_on_plates_1h_accepted` are rewritten in place as the ticket asks; the two disconnected tests are unchanged. The wiring tests' `FakeRunner.start_drift` gained the new `protections_ok` argument.
- ADR 0005 point 3 (drive the experiment's own waveform) is already what the tab's per-channel AC grid and shape selector do; nothing needed changing there.
- Still for ticket 48: the Overview warnings and the session recorder starting/stopping this same `SpikeRecorder` (session_started / session_stopped).
