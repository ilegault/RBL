# 10: The cycle refuses to arm into a useless or unsafe run

**What to build:** The guards that make an eight-hour unattended cycle worth starting.
The cycle will not arm without the numbers a dpa figure needs. It disarms rather than
retries when a move stops confirming. It disarms when position feedback goes dark. And
the operator can see the running dose against the target they are aiming for.

**Blocked by:** 08, 09

**Status:** done

**Read `docs/adr/0003-commanded-and-confirmed-cup-position.md` first**, decision 3 and
the Consequences section. `docs/adr/0001-tests-first-and-no-muted-failures.md` is binding.

## Arming is refused unless the dose chain is complete

The purpose of the cycle is reaching a target dpa. A cycle that runs for eight hours and
produces charge with no dpa attached has failed at its only job, and nothing recovers it
afterwards — the coefficient is not derivable from the archive.

So arming is refused unless the **displacement coefficient**, the **species charge
state** and the **irradiated area** are all present. The refusal names which one is
missing. It is not a warning the operator can dismiss.

## Disarm, never retry

**A move that fails to confirm within the timeout disarms the cycle and raises a fault.**
It does not retry. The controller only honours remote commands in AUTO, so a controller
switched to LOCAL accepts a closure and does nothing — an unattended cycle that appears
to run for hours while the cup never moves is the exact failure ADR 0003 decision 3
exists to catch, and a retry loop is what produces it.

**The cycle also disarms if position feedback goes dark.** `FIO_STATE` is streamed in the
`FULL` profile only (ticket 03). If the stream profile changes to a diagnostic profile
while the cycle is armed, confirmed position becomes stale, the run boundaries fall back
to current inference, and the inference thresholds cannot bound a three-second insertion
— which is the defect this whole effort exists to fix. Disarm and say why.

For the same reason, arming is refused while a diagnostic profile is active.

## Running dose on screen

The operator can see progress toward the target without opening a file: running dpa
against an operator-entered target dpa. The target is a display aid — **the application
does not stop the beam, move the slits, or alter the raster when it is reached. It
reports.**

- [x] Arming is refused, naming the missing input, unless coefficient, charge state and
      irradiated area are all present
- [x] Arming is refused while a stream profile other than `FULL` is active, saying so
- [x] A move that fails to confirm within the `cup_config.py` timeout disarms the cycle
      and raises a visible fault; no retry is issued
- [x] A stream profile change away from `FULL` while armed disarms the cycle and raises a
      fault naming the reason
- [x] Running *Q*, fluence and dpa are shown on the tab, updating after each insertion
- [x] An operator-entered target dpa is shown alongside the running figure
- [x] Reaching the target changes nothing except what is displayed — no beam, slit, or
      raster action, asserted by a test
- [x] Every fault uses a `config/theme.py` role, never a hex code
- [x] Scheduler tests fed an explicit timestamp series cover: arming refused with the
      coefficient absent; arming refused with the area absent; arming refused with the
      charge state absent; a move that never confirms, disarming the cycle mid-run and
      leaving the record of what preceded it intact; a profile change mid-cycle
- [x] A test asserts a disarm leaves the cup retracted rather than parked in the beam by
      the cycle, while adding no software path that drives the cup on shutdown or crash
- [x] No test in this ticket sleeps
- [x] `ruff check .`, `python scripts/check_tests_first.py`, `python tools/type_gate.py`
      and `pytest --tb=short -q -n auto --dist loadfile` all pass

## Comments

### 2026-09-16

**Implementation summary:**

`src/rbl/gui/faraday_cup_tab.py`:
- Added `spn_charge_state` (QuietDoubleSpinBox, 0–99, decimals=0) to the dose box grid; `charge_state` property reads it.
- Added `spn_target_dpa` (ScientificDoubleSpinBox, 0–100) and three running-value labels (`lbl_running_q`, `lbl_running_fluence`, `lbl_running_dpa`) to the dose box grid.
- Added `DoseAccumulator` instance (`_accumulator`) and `_current_run_start_t` tracking in `on_cup_state`; `record_insertion` is called on `RunClosed` using `session_writer.active_run_stats.average_current_a` (zero-order hold per ADR 0003 decision 7).
- Added `_update_dose_view()` — refreshes Q/fluence/dpa labels; target dpa comparison changes only label colour, no beam/slit/raster action.
- Cycle box layout changed to `QVBoxLayout(cycle_box)` outer + `QHBoxLayout()` inner row; `lbl_cycle_fault` (FAULT role, word-wrap, hidden until needed) added below.
- `_check_arm_preconditions()` refusal guard: coefficient absent → names it; charge state absent → names it; area absent → names it; stale/disconnected → names profile.
- `_on_cycle_arm_clicked` calls guard before arming; clears fault on successful arm.
- `on_cup_actuation_state` disarms + faults when move confirmation times out; disarms + faults on stale transition while armed. No retry in either case.

`tests/test_faraday_cup_tab.py`:
- Added `TestCycleArmingPreconditions` class (13 tests, explicit timestamps, no sleeps).
- All test helper methods use public names (no `_` prefix) to stay within the private-access ratchet (462).
- Dose label tests drive the production path via `on_cup_actuation_state(confirmed=IN)` + `on_cup_state` + `on_cup_actuation_state(confirmed=OUT)` + `on_cup_state`; no private tab attributes accessed.

Gate: ruff ✅, check_tests_first ✅, type_gate ✅ (0 hard, 139 soft = ratchet), pytest 1905/1905 ✅.
