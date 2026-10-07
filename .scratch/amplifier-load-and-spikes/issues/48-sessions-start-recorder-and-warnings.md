# 48: Sessions run the spike recorder, and the Overview tab warns without blocking

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 47, 36, 33

**Spec:** `.scratch/amplifier-load-and-spikes/spec.md` (Part E)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `docs/adr/0006-amplifier-faults-are-observed-not-stopped-during-a-session.md`

**Read first:** `src/rbl/services/session_recorder.py` (`session_started`, `session_stopped`, `_write_event`), `src/rbl/gui/app.py` (`MainWindow.__init__` - wiring), `src/rbl/gui/overview_tab.py` (the status area near `lbl_hv_interlock`), `src/rbl/config/characterization_history.py` (`newest_on_plates_c_pf`), `tests/test_e2e_session.py` (how a `MainWindow` is built in tests).

## What to build

- `MainWindow` connects `SessionRecorder.session_started(folder)` to `SpikeRecorder.start(folder)`,
  `session_stopped` to `stop()`, `Beamline.raw_window_ready` to `on_window`, and
  `FuncGenSetpoints.changed` to `on_setpoint_changed`. Each spike is also written to the
  session's event log through `SessionRecorder._write_event("spike", <summary>)`.
- The Overview tab gains an "Amplifier warnings" list (not a dialog) and a spike count per plate.
  A warning is added for: a spike with `peak_ma > 20`; a spike with `gap_to_previous_s < 0.1`;
  a captured reference that differs by more than 10 % from k x f x C x V using
  `newest_on_plates_c_pf` (skipped for an unmeasured plate). Each warning has a Dismiss button
  and stays until it is clicked.

Tests may fake: the stream payload and setpoints; the session folder is real (temp).

## Acceptance criteria

- [ ] Starting a session in a test `MainWindow` makes `SpikeRecorder.is_running()` True; stopping it makes it False; `spikes.csv` is created inside that session's folder.
- [ ] A synthetic 25 mA spike on X+ adds one warning whose text contains `25` and `X+`, and the X+ spike count reads `1`.
- [ ] Two spikes 50 ms apart add a warning containing `100 ms`.
- [ ] A reference 20 % above the prediction from a measured 1600 pF result adds a warning containing `differs`; an unmeasured plate adds none.
- [ ] With `QMessageBox.exec`, `QMessageBox.warning` and `QDialog.exec` replaced by functions that fail the test, all of the above pass, and a warning remains listed until its Dismiss button is clicked.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
