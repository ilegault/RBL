# 47: The spike recorder: a reference per operating point, a spike file, and waveforms

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 34, 46

**Spec:** `.scratch/amplifier-load-and-spikes/spec.md` (Part E)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `docs/adr/0006-amplifier-faults-are-observed-not-stopped-during-a-session.md`

**Read first:** `src/rbl/state/beamline.py` (`raw_window_ready`), `src/rbl/state/setpoints.py` (`FuncGenSetpoints.changed`, `AXIS_CHANNELS`), `src/rbl/config/hardware_config.py` (`AMP_CHANNEL_MAP`, `CURRENT_MONITOR_MA_PER_VOLT`, `VOLTAGE_MONITOR_KV_PER_VOLT`), `src/rbl/services/load_characterizer.py` (`on_window` - payload shape), `src/rbl/hardware/spike_detector.py` (from 46).

## What to build

A new `src/rbl/services/spike_recorder.py`, a QObject with `start(output_dir)`, `stop()`,
`is_running()`, `on_window(payload)`, `on_setpoint_changed(key, params)`,
`save_pre_event(plate, seconds=10)`, and signals `spike_recorded(dict)`,
`reference_captured(str, dict)`.

- While running, for every plate whose setpoint has `output_on` and non-zero amplitude, it
  converts the current-monitor waveform to mA, collects 60 s for `capture_reference`, then feeds
  a `SpikeDetector`. A setpoint change for that plate's channel discards the reference and
  starts a new 60 s capture. No spikes are recorded for a plate without a reference.
- It keeps a 10 s ring buffer of both monitors per driven plate.
- Each spike appends a row to `<output_dir>/spikes.csv` with columns `time_iso, plate_position,
  amplifier_serial, duration_s, peak_ma, peak_is_lower_bound, charge_uc, gap_to_previous_s,
  sample_interval_s, reference_ma, threshold_ma`, and writes 2 s before and after into
  `<output_dir>/spikes/<plate>_<YYYYMMDDTHHMMSS_ffffff>.csv` (columns `t_s, voltage_kv, current_ma`);
  a spike inside an open file's after-window extends that file instead of starting another.
- `save_pre_event` writes the last `seconds` for that plate to `spikes/<plate>_pre_event_<stamp>.csv`.
- It never calls anything on a generator, an amplifier drive or a setpoint.

Tests may fake: the payload (synthetic windows), the setpoints object, and the clock
(constructor `now_fn`). Output files are real (temp folder).

## Acceptance criteria

- [ ] 60 s of a steady 2 mA current payload then one 6 mA, 1 ms excursion writes exactly one `spikes.csv` row for that plate with `peak_ma` 6 within 5 %, and one waveform file spanning 2 s either side.
- [ ] An excursion in the first 60 s writes no row; after a setpoint change, an excursion in the next 60 s writes no row.
- [ ] Two spikes 50 ms apart: the second row has `gap_to_previous_s` 0.05 within one sample interval, and both land in one waveform file.
- [ ] A plate whose setpoint has `output_on=False` produces no rows whatever its payload contains.
- [ ] With every setpoint-mutating, generator and drive method replaced by a function that fails the test, a full run with spikes passes.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
