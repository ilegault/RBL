# 56: Spikes record their time at the rail and the scale they were measured with

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 55

**Spec:** `.scratch/current-monitor-scale/spec.md` (Part E)
**Binding:**
- `docs/adr/0006-amplifier-faults-are-observed-not-stopped-during-a-session.md`;
- `docs/adr/0007-current-monitor-scale-is-2-ma-per-volt.md`;
- `docs/adr/0001-tests-first-and-no-muted-failures.md`.

**Read first:**
- `CONTEXT.md`: "At the rail", "Current spike", "Spike threshold".
- `src/rbl/hardware/spike_detector.py`: the module docstring, `Spike`, `SpikeDetector.feed`,
  `_begin`, `_extend`, `_close`.
- `src/rbl/services/spike_recorder.py`:
  - the module docstring around its lines 35 and 43 (the 100 ms / 10 mA recovery period);
  - `SPIKE_COLUMNS`;
  - `on_window` (the loop that converts `i_entry["waveform"]`);
  - `_on_plate_window`, `_record`.
- `src/rbl/gui/load_characterization_tab.py`: `_parse_spike`, `add_spike`, `_redraw_spikes`.
- `tests/test_load_characterization_tab.py`: `test_a_lower_bound_peak_is_drawn_hollow`
  (copy its hollow-marker check).
- `src/rbl/hardware/amp_monitor.py`: `is_at_rail`.

## What to build

The current monitor cannot read above ~20 mA (ADR 0007). A spike that reaches the rail
therefore has no measurable peak, only a duration at the rail. This ticket records that
duration, marks such a peak as a lower bound, and stamps every spike row with the scale.

1. **`Spike` fields.** `Spike` gains `rail_s: float` (time at the rail inside the spike)
   and `at_rail: bool` (`rail_s > 0`).
2. **`SpikeDetector.feed` signature.** It becomes
   `feed(samples_ma, t0_s, at_rail=None)`, where `at_rail` is an optional bool array the
   same length as `samples_ma`.
3. **A rail sample is always over the threshold.** The over-threshold mask becomes
   `(abs(I) > threshold) | at_rail`. This is a requirement, not a preference. With a
   12 mA reference the threshold is 24 mA, above anything the monitor can read, and
   without this rule a clamped amplifier would never produce a spike row.
4. **Rail time and the lower bound.**
   - Each rail sample inside a spike adds `dt` to its `rail_s`, and a run that crosses
     windows keeps adding.
   - `peak_is_lower_bound` is the existing rule OR `at_rail`.
5. **Unchanged without a mask.** With `at_rail=None`, the detector behaves exactly as it
   does today, with `rail_s == 0`.
6. **The detector stays pure.** `spike_detector.py` keeps importing numpy only, with no
   `rbl` imports. It is tested with plain arrays, and the caller supplies the rail mask.
7. **`SpikeRecorder`** computes `is_at_rail(raw current volts)` for each driven plate's
   window and passes it through `_on_plate_window` to `feed`.
   - `SPIKE_COLUMNS` gains, at the end, `"rail_s"`, `"at_rail"` and
     `"current_monitor_ma_per_volt"`.
   - `_record` writes `spike.rail_s`, `spike.at_rail`, and the value of
     `hardware_config.CURRENT_MONITOR_MA_PER_VOLT` read at call time.
   - Reference capture is unchanged.
   - Rewrite the docstring sentences that cite the 100 ms / 10 mA recovery period: the
     units are assumed to have no burst (`docs/hardware/eel5000-manufacturer-notes.md`
     section 3.4).
8. **Spike chart.**
   - `_parse_spike` reads `at_rail`. A missing column or empty value counts as False, so
     spike files written before this ticket still open.
   - A spike with `at_rail` is drawn at `y = AMP_CONTINUOUS_RATING_MA` with a hollow
     marker, the same hollow style as a lower bound, at x = its duration.

Tests may fake: stream payloads, setpoints and the clock. The spike folder is a real temp
folder.

## Guardrails

- The recorder still commands nothing: no output off, no setpoint change, no dialog
  (ADR 0006). The existing `test_the_recorder_never_commands_anything` must still pass
  unchanged.
- Spike threshold stays `max(2 x reference, reference + 5 x noise)`. The only change is
  that a rail sample always counts as over it.
- Overview warnings stay out of scope. They are ticket 48.

## Acceptance criteria

- [ ] **Rail inside a spike.** `test_a_rail_segment_inside_a_spike_sets_rail_time_and_lower_bound`
  (new, `tests/test_spike_detector.py`):
  - setup: reference 2 mA, noise 0, `dt=1e-4`; 20 samples at 25 mA whose middle 10 are
    marked `at_rail`;
  - result: exactly one spike, with `rail_s == 0.001` (abs 1e-12), `at_rail` True and
    `peak_is_lower_bound` True.
- [ ] **Rail above the threshold.**
  `test_a_rail_sample_is_a_spike_even_when_the_threshold_is_above_the_rail` (new):
  - setup: reference 12 mA, noise 0 (so the threshold is 24 mA); 30 samples at 19.9 mA
    marked `at_rail`;
  - result: one spike with `rail_s == 0.003`.
- [ ] **No mask.** `test_without_a_rail_mask_spikes_are_unchanged` (new): a 6 mA spike on a
  2 mA quiet reference, fed with no `at_rail`, gives `rail_s == 0` and `at_rail` False,
  with the same duration and peak as today.
- [ ] **CSV columns.** `test_spikes_csv_carries_rail_time_and_the_scale` (new,
  `tests/test_spike_recorder.py`):
  - the header ends with `rail_s, at_rail, current_monitor_ma_per_volt`;
  - for a spike whose raw current samples sit at 10.0 V for 20 samples, the row has
    `at_rail == "True"`, `float(rail_s) == 20 * dt` (abs 1e-9) and
    `float(current_monitor_ma_per_volt) == 2.0`.
- [ ] **Chart.**
  - `test_a_rail_spike_is_drawn_hollow_at_the_continuous_rating` (new,
    `tests/test_load_characterization_tab.py`): a live row with `at_rail` "True" and
    `peak_ma` 19.9 is drawn at y 20.0 with face alpha 0.
  - `test_an_old_spike_file_without_rail_columns_still_opens` (new): a `spikes.csv`
    written with today's columns only opens and draws its markers.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
