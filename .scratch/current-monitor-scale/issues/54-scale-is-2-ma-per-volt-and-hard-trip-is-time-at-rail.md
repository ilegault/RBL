# 54: The current monitor reads 2 mA per volt, and the hard trip is time at the rail

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 53

**Spec:** `.scratch/current-monitor-scale/spec.md` (Parts A, B, C)
**Binding:**
- `docs/adr/0007-current-monitor-scale-is-2-ma-per-volt.md`;
- `docs/adr/0006-amplifier-faults-are-observed-not-stopped-during-a-session.md`;
- `docs/adr/0001-tests-first-and-no-muted-failures.md`.

**Deletes tests:** tests/test_calibration_runner.py::test_a_40_ma_window_during_a_pass_on_the_plates_does_not_end_it, tests/test_load_characterizer.py::test_one_sample_over_the_hard_trip_ends_it_with_hard_trip, tests/test_load_characterizer.py::test_one_sample_over_the_hard_trip_ends_the_ladder_before_the_point_is_emitted, tests/test_load_characterizer.py::test_a_sample_just_under_the_hard_trip_is_not_a_trip, tests/test_load_characterizer.py::test_a_negative_railed_sample_also_trips

**Read first:**
- `docs/adr/0007-current-monitor-scale-is-2-ma-per-volt.md`, and
  `docs/hardware/eel5000-manufacturer-notes.md` sections 3.1, 3.2 and 6.
- `CONTEXT.md`: "Hard trip", "Soft trip", "At the rail", "Current monitor scale".
- `src/rbl/services/calibration_runner.py`:
  - `_check_overcurrent`: the HARD branch and its docstring;
  - `_trip`;
  - where `_trip_blank_left` is set when a setpoint is commanded;
  - the metadata dict holding `"trip_hard_ma"`.
- `src/rbl/services/load_characterizer.py`:
  - `_hard_tripped`;
  - the `on_window` branch that calls it;
  - `_advance`;
  - the module docstring paragraph on `hard_trip`.
- `src/rbl/config/calibration_config.py`: the `CAL_AC_TRIP_MA` and `CAL_TRIP_HARD_MA`
  blocks.
- `src/rbl/hardware/amp_monitor.py`: `RailTracker` and `is_at_rail` (from ticket 53),
  plus the `monitor_to_ma` docstring and the `__main__` self-test.
- `src/rbl/config/labjack_stream_config.py`: the AIN comments.

## What to build

The manufacturer says the EEL5000 current monitor is 1 V = 2 mA, not the manual's
1 V = 10 mA (ADR 0007). The LabJack's ±10 V input therefore tops out at ±20 mA, so a
dead short reads 20 mA, not "a huge number". The scale change and the new hard-trip
definition must land in the same PR. If only the scale changed, the 60 mA hard trip
could never fire.

1. **`hardware_config.CURRENT_MONITOR_MA_PER_VOLT = 2.0`**, with this comment: "1 V == 2 mA;
   +/-10 V == +/-20 mA. Manufacturer's statement of 2026-10-07; the manual's 1 V == 10 mA
   is wrong. Not bench-verified. docs/adr/0007-current-monitor-scale-is-2-ma-per-volt.md".
   The "see EEL5000 manual" header above it now also cites the ADR.
2. **`calibration_config`**:
   - Add `HARD_TRIP_RAIL_S = 0.005`. Its comment says: a square edge into even 3000 pF
     at a 10 kV step, clamped at 20 mA, leaves the rail in 1.5 ms; a dead short holds it.
   - `CAL_TRIP_HARD_MA` no longer exists.
   - `CAL_AC_TRIP_MA = 19.0`. Its comment says it is the calibration soft-trip level and
     the `ac_max_peak_kv` ladder-sizing level, just under the rail (~19.8 mA). It also
     says this is not the continuous rating, which is `AMP_CONTINUOUS_RATING_MA`.
   - Soft-trip blanking, minimum duration and consecutive windows are unchanged.
3. **`CalibrationRunner`** owns one `RailTracker` for the driven current channel.
   - The tracker is `reset()` wherever `_trip_blank_left` is re-armed on a new setpoint.
   - In `_check_overcurrent`, the HARD branch feeds the raw (signed, un-abs'd) window to
     the tracker, with the same `dt_s` expression the soft branch uses.
   - When the result is `>= HARD_TRIP_RAIL_S`, it calls `_trip(peak_ma, CAL_AC_TRIP_MA,
     "hard", rail_s)`. The error text then contains the rail duration in ms.
   - The HARD check still runs before blanking, on every window.
   - In the run metadata, the key `"trip_hard_ma"` becomes
     `"hard_trip_rail_s": HARD_TRIP_RAIL_S`.
   - Rewrite the docstrings to the rail definition.
4. **`LoadCharacterizer._hard_tripped`** feeds the raw current window to one
   `RailTracker`.
   - The tracker is `reset()` in `_advance` on each new step.
   - `_hard_tripped` returns True when the result is `>= HARD_TRIP_RAIL_S`.
   - The rule name stays `"hard_trip"`.
   - Rewrite the module docstring's `hard_trip` paragraph to the rail definition.
5. **No source file states the old scale.**
   - Fix the text in `labjack_stream_config.py` (both AIN comments), `hardware_config.py`
     and `amp_monitor.py`: the `monitor_to_ma` docstring and the `__main__` comment and
     assertions, moved to the 2 mA scale.
   - Leave the NaN guard in `monitor_to_ma` as it is; ticket 55 handles it.
6. **New file `tests/test_current_monitor_scale.py`** for the contract tests below.
7. **Hard-trip tests:**
   - Delete the five tests on the `Deletes tests:` line above. Their names describe a
     one-sample, 60 mA trip that no longer exists, and in the case of the 40 mA test, a
     current the monitor can no longer represent.
   - Write the new tests named below in their place, in the same classes.
   - Remove the `CAL_TRIP_HARD_MA` import from `tests/test_load_characterizer.py`.

Tests may fake: the function generators and the stream payloads (synthetic arrays,
built with `ma_to_monitor`, or raw volts where a test is about the rail).

## Guardrails

- **Sessions and drift passes never trip.** Nothing in this ticket adds a current check to
  `SpikeRecorder`, `RegulationResponder`'s observe-only mode, `SessionRecorder`, or the
  ON_PLATES drift pass (ADR 0006).
- **One scale.** No second scale factor, and no "legacy" or compatibility constant for 10.0.
- **Planner margins unchanged.** `MARGIN_AMBER_MA` and `MARGIN_RED_MA` in `raster_plan.py`
  keep their values.
- **Failing tests are escalated, not muted.** If a test outside the ones named here starts
  failing, do not change its assertion, tolerance or inputs. Escalate per `AGENTS.md`
  ("Fix or escalate"), saying which test and why.

## Acceptance criteria

- [x] **Scale.**
  - `test_five_volts_is_ten_milliamps` (new, `tests/test_current_monitor_scale.py`):
    `monitor_to_ma(5.0) == 10.0`, `ma_to_monitor(20.0) == 10.0` and
    `ma_unclamped(-10.0) == -20.0`. Its docstring cites ADR 0007.
  - `test_scale` in `TestCurrentMonitor`, `tests/test_amp_monitor.py` (rewritten in
    place): the table is `(0.1, 0.2), (1.0, 2.0), (5.0, 10.0), (10.0, 20.0),
    (-10.0, -20.0)`. The class docstring says 1 V == 2 mA (ADR 0007).
- [x] **Contract.**
  - `test_the_scale_is_assigned_only_in_hardware_config` (new): over `src/rbl/**/*.py`,
    the regex `^\s*CURRENT_MONITOR_MA_PER_VOLT\s*=` matches only in
    `src/rbl/config/hardware_config.py`.
  - `test_no_source_file_states_the_old_scale` (new): the case-insensitive regex
    `1\s*V\s*={1,2}\s*10\s*mA` matches no file under `src/rbl/`.
- [x] **Calibration runner, hard trip.**
  - `test_railed_current_monitor_hard_trip_fires` (rewritten in place, assertions
    unchanged): its docstring explains that 40 samples at 10.0 V, at 2.5 ms per sample,
    is 100 ms at the rail.
  - `test_a_one_millisecond_rail_does_not_hard_trip` (new): a payload with
    `sample_period` 1e-4 has 10 samples at 10.0 V among 400 at 0 V. No `overcurrent` is
    emitted and the runner is not IDLE.
- [x] **Calibration runner, soft trip.**
  - `test_19p5_ma_sustained_soft_trips` (new): after the blanking windows, feeding
    `CAL_TRIP_CONSEC_WINDOWS` windows at `ma_to_monitor(19.5)` emits `overcurrent` with
    kind `sustained`.
  - `test_18p5_ma_sustained_does_not_trip` (new): the same sequence at
    `ma_to_monitor(18.5)` emits nothing.
- [x] **Drift pass on the plates.**
  `test_a_railed_monitor_during_a_pass_on_the_plates_does_not_end_it` (new, in
  `TestDriftLoadConditionGuard`): with the same setup as the test it replaces, windows at
  `current_v=10.0` for longer than `HARD_TRIP_RAIL_S` leave the ON_PLATES drift pass
  running, and the outputs stay on.
- [x] **Load characterizer.**
  - `test_a_rail_held_for_6_ms_ends_the_clamp_test_with_hard_trip` (new, `TestClampTest`).
  - `test_a_rail_held_for_6_ms_ends_the_ladder_before_the_point_is_emitted` (new,
    `TestModeCLadderAborts`).
  - `test_a_negative_rail_held_for_6_ms_also_trips` (new).
  - `test_a_150_us_rail_on_every_edge_is_not_a_trip` (new): a Mode C rung whose every
    edge sits at 10.0 V for 150 µs completes with no abort rule.
  - In all four, the rail is built from raw volts at 10.0 V, the run ends with
    `abort_rule == "hard_trip"` where a trip is expected, and the run has no abort rule
    where it is not.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments

## Escalation — 2026-10-07
Repo: RBL   Ticket: 54 The current monitor reads 2 mA per volt, and the hard trip is time at the rail   Branch: ticket/current-monitor-scale-54-scale-is-2-ma-per-volt-and-hard-trip-is-time-at-rail
Goal: Update CURRENT_MONITOR_MA_PER_VOLT from 10.0 to 2.0, replace CAL_TRIP_HARD_MA with HARD_TRIP_RAIL_S = 0.005, set CAL_AC_TRIP_MA = 19.0, and update CalibrationRunner and LoadCharacterizer hard trips to use RailTracker.
Attempt 1: Implemented all core changes across hardware_config, calibration_config, labjack_stream_config, amp_monitor, calibration_runner, load_characterizer, and all tests named in the ticket acceptance criteria. All ticket acceptance criteria pass, ruff passes, check_tests_first passes, type_gate passes, and layering ratchet passes.
Attempt 2: Ran full test suite across the repo. Discovered that 9 existing tests outside ticket 54 fail because they hardcode assertions or fixtures assuming the old 10 mA/V scale factor rather than converting through `ma_to_monitor` or adjusting expectations for the 2 mA/V scale. Per ticket guardrail ("If a test outside the ones named here starts failing, do not change its assertion, tolerance or inputs. Escalate per AGENTS.md ('Fix or escalate'), saying which test and why.") and ADR 0001, these external tests must not be modified in this ticket without escalation.
Failing output (exact, trimmed to the relevant lines):
```
FAILED tests/test_beamline.py::TestLabjackIngestion::test_amp_voltage_and_current_conversion
    assert ch.rms_ma == pytest.approx(5.0)            # 1 V == 10 mA
    assert 1.0 == 5.0 ± 5.0e-06 (Obtained: 1.0, Expected: 5.0)
FAILED tests/test_beamline.py::TestSnapshotsCarryWhatTheTabsNeed::test_full_resolution_window_is_scaled_once_here
    assert ch.window_ma.max() == pytest.approx(5.0)    # 0.5 V -> 5 mA
    assert np.float64(1.0) == 5.0 ± 5.0e-06 (Obtained: 1.0, Expected: 5.0)
FAILED tests/test_labjack_link.py::TestAmpWaveformConversion::test_dc_ma_is_pre_converted_mean_of_current_waveform
    assert ch.dc_ma == pytest.approx(10.0)
    assert 2.0 == 10.0 ± 1.0e-05
FAILED tests/test_labjack_link.py::TestAmpWaveformConversion::test_rms_ma_from_dc_current_waveform
    assert ch.rms_ma == pytest.approx(10.0, rel=1e-3)
    assert 2.0 == 10.0 ± 0.01
FAILED tests/test_amp_tab_isolation.py::TestAmpTabIgnoresLogAmps::test_current_conversion
    assert abs(ma - 10.0) < 1e-9
    assert np.float64(8.0) < 1e-09 (Obtained: 2.0, Expected: 10.0)
FAILED tests/test_amp_tab_isolation.py::TestAmpTabIgnoresLogAmps::test_rendered_measured_voltage_and_current_dc
    assert amp.lbl_cur["X+"].text() == "+10.000 mA mean"
    AssertionError: assert '+2.000 mA mean' == '+10.000 mA mean'
FAILED tests/test_amp_single_channel.py::TestSingleChannelMode::test_current_target_waveform_follows_selection
    assert "5.000 mA" in tab.lbl_cur["Y-"].text()
    AssertionError: assert '5.000 mA' in '+1.000 mA mean'
FAILED tests/test_calibration_runner.py::TestRegulationState::test_current_limited_when_voltage_low_current_pinned
    assert state == "current_limited"
    AssertionError: assert 'amp_off' == 'current_limited' (seeded with i_v=1.95, which was 19.5 mA under 10 mA/V, but is 3.9 mA under 2 mA/V)
FAILED tests/test_calibration_runner.py::TestDriftLoadConditionGuard::test_an_excursion_is_logged_once_not_every_window
    assert len(per_channel) == 1
    AssertionError: assert 0 == 1 (uses current_v=4.0, which was 40 mA under 10 mA/V, but is 8 mA under 2 mA/V, below 19 mA trip threshold)
```
Decision needed: Per Ticket 54 guardrail ("If a test outside the ones named here starts failing, do not change its assertion, tolerance or inputs. Escalate per AGENTS.md ('Fix or escalate'), saying which test and why.") and ADR 0001, should these 9 tests be updated in this branch to reflect the new 1 V = 2 mA scale (using `ma_to_monitor`), or should ticket 53 be reopened/a follow-up ticket created to convert them?


## Resolution — 2026-10-08
Decision (owner, via planning session): the 9 escalated tests are updated on this branch. No follow-up ticket and no reopening of 53.
Classification: **harness defect**. The fixtures fed raw volts chosen for 1 V = 10 mA; production code correctly follows ADR 0007. Nothing was muted: every expected mA value, state and label is unchanged, and only the input volts now go through `ma_to_monitor(...)`, so each test still fails if the code is wrong.
Changed: `test_beamline.py` (2 tests), `test_labjack_link.py` (2), `test_amp_tab_isolation.py` (2, via `FULL_READING`), `test_amp_single_channel.py` (1), `test_calibration_runner.py` (2).
Two inputs were re-chosen because the old value is unrepresentable at 20 mA full scale: the "40 mA" excursion is now 19.5 mA (above the 19.0 mA soft-trip level, so the excursion log still fires), and the pinned-current seed `i_v=1.95` is now `ma_to_monitor(19.5)`.
Gate: `ruff check .`, `check_tests_first.py`, `type_gate.py` and the full pytest suite all pass locally (2342 passed).
