# 49: The regulation detector watches sessions in observe-only mode

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 47, 48

**Spec:** `.scratch/amplifier-load-and-spikes/spec.md` (Part F)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `docs/adr/0006-amplifier-faults-are-observed-not-stopped-during-a-session.md`

**Read first:** `src/rbl/services/regulation_response.py` (`RegulationResponder.handle_fault`), `src/rbl/services/regulation_monitor.py` (`RegulationMonitor.evaluate`), `src/rbl/hardware/ac_metrics.py` (`fundamental`), `src/rbl/state/setpoints.py` (`FuncGenSetpoints`), `src/rbl/services/trip_history.py` (`append_trip`), `tests/test_regulation_response.py` (`test_stops_the_channel_on_fault`).

## What to build

- `RegulationResponder` gains `mode` (`"stop"` default, `"observe"`). In `observe`, `handle_fault`
  never calls `amp_drive.output_off` and never creates a dialog; it appends to the trip history
  with operating conditions and `mode: "observe"`, calls `spike_recorder.save_pre_event(label)`
  when the state is `amp_off`, and emits `warning(str)`. `stop` mode is unchanged.
- `MainWindow` builds a `RegulationMonitor` and an observe-mode `RegulationResponder`, and while a
  session runs feeds the monitor on each window: commanded kV per plate from the setpoint
  (`amp_vpp / 2`, since 1 V at the generator is 1 kV at the plate), measured kV from `fundamental`
  of the voltage monitor at the setpoint frequency (window mean for DC), current from the current
  monitor, limit 20 mA. `warning` goes to the Overview warnings list from 48.

Tests may fake: the amplifier drive (a recording object), the stream payload, the setpoints.

## Acceptance criteria

- [ ] In observe mode a confirmed `current_limited` fault leaves the fake drive's `output_off` calls empty, with `QDialog.exec` replaced by a function that fails the test.
- [ ] The trip history gains exactly one record with `state: 'current_limited'` and `mode: 'observe'`.
- [ ] An `amp_off` fault makes `save_pre_event` write a `spikes/X+_pre_event_*.csv` file in the session folder.
- [ ] In a test session, windows whose voltage fundamental is 40 % of commanded for the debounce count add an Overview warning naming the plate.
- [ ] `test_stops_the_channel_on_fault` passes unchanged.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
