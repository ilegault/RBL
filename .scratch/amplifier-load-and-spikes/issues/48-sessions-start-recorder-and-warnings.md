# 48: Sessions run the spike recorder, and the Overview tab warns without blocking

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 47, 36, 33, 56, 58

**Spec:** `.scratch/amplifier-load-and-spikes/spec.md` (Part E), as amended by `.scratch/current-monitor-scale/spec.md` (Part E, "Warnings")
**Binding:**
- `docs/adr/0001-tests-first-and-no-muted-failures.md`;
- `docs/adr/0006-amplifier-faults-are-observed-not-stopped-during-a-session.md`;
- `docs/adr/0007-current-monitor-scale-is-2-ma-per-volt.md`.

**Read first:**
- `src/rbl/services/session_recorder.py`: `session_started`, `session_stopped`,
  `_write_event`.
- `src/rbl/gui/app.py`: the wiring in `MainWindow.__init__`.
- `src/rbl/gui/overview_tab.py`: the status area near `lbl_hv_interlock`.
- `src/rbl/config/characterization_history.py`: `newest_on_plates_c_pf`.
- `src/rbl/services/spike_recorder.py`: `spike_recorded` and the row's `rail_s` and
  `at_rail` fields (from ticket 56).
- `tests/test_e2e_session.py`: how a `MainWindow` is built in tests.
- `docs/hardware/eel5000-manufacturer-notes.md`: sections 3.3, 3.4 and 6, item 5.

## What to build

1. **Wiring in `MainWindow`.**
   - Connect `SessionRecorder.session_started(folder)` to `SpikeRecorder.start(folder)`,
     and `session_stopped` to `stop()`.
   - Connect `Beamline.raw_window_ready` to `on_window`, and `FuncGenSetpoints.changed`
     to `on_setpoint_changed`.
   - Also write each spike to the session's event log through
     `SessionRecorder._write_event("spike", <summary>)`.
2. **Overview tab.** It gains an "Amplifier warnings" list (not a dialog) and a spike
   count per plate. A warning is added for:
   - **A spike at the rail.** A spike row with `at_rail` True gives the text
     `current at the rail for <rail_s in ms, one decimal> ms on <plate>`. The current
     monitor cannot read above ~20 mA (ADR 0007), so the rail duration is what is
     reported, never a peak.
   - **A reference off its prediction.** A captured reference that differs by more than
     10 % from k x f x C x V, using `newest_on_plates_c_pf`. An unmeasured plate is
     skipped.
3. **Not built:** a warning for two spikes under 100 ms apart, or any warning keyed to a
   20 mA or 100 mA peak. The units are assumed to be in the 20 mA DC configuration, which
   has no burst and no recovery period
   (`docs/hardware/eel5000-manufacturer-notes.md` section 3.4).
4. **Dismissal.** Each warning has a Dismiss button and stays until it is clicked.

Tests may fake: the stream payload and setpoints. The session folder is real (temp).

## Acceptance criteria

- [ ] **Session start and stop.** `test_starting_a_session_runs_the_spike_recorder` (new,
  `tests/test_amplifier_warnings.py`): starting a session in a test `MainWindow` makes
  `SpikeRecorder.is_running()` True and creates `spikes.csv` in that session's folder.
  Stopping it makes `is_running()` False.
- [ ] **Rail warning.** `test_a_spike_at_the_rail_warns_with_its_duration` (new): a
  synthetic spike on X+ whose raw current monitor sits at 10.0 V for 3 ms adds one warning
  containing `at the rail`, `3.0 ms` and `X+`, and the X+ spike count reads `1`.
- [ ] **No warning below the rail.** `test_a_spike_below_the_rail_does_not_warn` (new): a
  6 mA spike on a 2 mA reference adds a row and bumps the count, but adds no warning.
- [ ] **No gap warning.** `test_spikes_close_together_do_not_warn` (new): two non-rail
  spikes 50 ms apart add no warning.
- [ ] **Prediction warning.** `test_a_reference_off_the_prediction_warns` (new):
  - setup: a reference 20 % above the prediction from a measured on-plates result written
    through `write_result`;
  - result: a warning containing `differs`. An unmeasured plate adds none.
- [ ] **No dialogs, and dismissal.**
  `test_warnings_never_open_a_dialog_and_stay_until_dismissed` (new):
  - setup: `QMessageBox.exec`, `QMessageBox.warning` and `QDialog.exec` replaced by
    functions that fail the test;
  - result: a rail warning appears, stays listed after more windows, and goes only when
    its Dismiss button is clicked.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments

2026-10-07 (planner): Rewritten for ADR 0007. The monitor tops out at ~20 mA, so the
"spike above 20 mA" warning became "current at the rail for N ms". The "two spikes under
100 ms apart" warning is dropped until the 100 mA configuration is understood. Now blocked
by 56 (rail fields on spikes) and 58 (results carry their scale, so the prediction uses
only current-scale capacitances).
