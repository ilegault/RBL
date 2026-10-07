# 45: Clamp test: find the current where an amplifier stops following its input

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 44, 39

**Spec:** `.scratch/amplifier-load-and-spikes/spec.md` (Part C)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `docs/adr/0006-amplifier-faults-are-observed-not-stopped-during-a-session.md` (decision 2)

**Read first:** `src/rbl/services/load_characterizer.py` (`start_mode_a`, `_finish_mode_a_point` - copy its use of `fundamental`), `src/rbl/hardware/regulation.py` (`regulation_ratio`), `src/rbl/gui/load_characterization_tab.py` (`_selected_mode`, `_on_run_clicked`).

## What to build

- A new `Mode.CLAMP` and `start_clamp_test(amp_label, peak_kv=1.0, freq_list=None)`: triangle at
  `peak_kv`, frequencies `CLAMP_FREQ_LADDER_HZ = [250, 500, 750, 1000, 1500, 2000, 2500, 3000, 4000, 5000]`.
- Each step records the voltage fundamental (kV), the current fundamental (mA) and
  `regulation_ratio(measured_kv, peak_kv)`. The test stops at the first step with a ratio below
  `CLAMP_RATIO_THRESHOLD = 0.95`; that step's current is `clamp_ma`. If no step falls below,
  `clamp_ma` is `None` and the status says `not reached`.
- The hard trip from 44 applies. The result is written with `method` `clamp_test` and `values`
  `{"clamp_ma", "clamp_freq_hz", "peak_kv"}`.
- The tab gains a radio button "Clamp test" that runs it.

Tests may fake: the function generator and stream windows.

## Acceptance criteria

- [ ] Synthetic steps holding 1.0 kV through 1500 Hz and 0.8 kV at 2000 Hz: the test stops after 2000 Hz with `clamp_freq_hz == 2000`, and 2500 Hz is never commanded.
- [ ] `clamp_ma` equals the synthetic current fundamental at the stopping step to 1e-6.
- [ ] A run whose ratio never drops writes `clamp_ma: null` and the status text contains `not reached`.
- [ ] Selecting Clamp test and clicking Run (fakes connected) calls `start_clamp_test` with the selected plate.
- [ ] One sample above `CAL_TRIP_HARD_MA` stops the clamp test with `hard_trip`.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
