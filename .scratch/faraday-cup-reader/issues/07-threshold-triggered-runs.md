# 07: Threshold-triggered acquisition runs, with manual override

**What to build:** The application notices when the cup goes into the beam and starts an
acquisition run on its own; it notices when the cup comes out and ends the run. The tab
shows plainly whether a run is active. An operator can force a run to start or stop at
any time, overriding the detector completely.

Nothing is written to disk in this ticket. What this delivers is: insert the cup, watch
the tab say it is acquiring; withdraw it, watch the run close.

**Blocked by:** 06

**Status:** done

**Read `docs/adr/0002-cup-acquisition-triggered-by-current.md` first.** It records why
insertion is inferred rather than commanded, and decision 6 constrains how this is built.

Two structural requirements:

- The state machine is a **pure object in the services layer**. It does not import Qt and
  it does not call the clock. Timestamps are inputs. This is what makes the debounce and
  release intervals testable exactly rather than by sleeping.
- It exposes **one value** answering "is the cup in the beam?", derived today from
  current. A future commanded-and-confirmed cup position must be able to replace that
  value at its source without the run logic changing. Do not scatter threshold
  comparisons through the run logic.

- [x] A run opens when cup current stays above the arm threshold for the debounce interval
- [x] A run closes when cup current stays below the release threshold for the release interval
- [x] The release threshold is lower than the arm threshold
- [x] Thresholds and intervals live in the config layer; starting values are 0.5 µA,
      0.25 µA, 1.0 s and 3.0 s
- [x] Polling moves to 10 Hz while a run is active and back to 2 Hz when it closes
- [x] The tab shows whether a run is active
- [x] Force start opens a run regardless of current; force stop closes one regardless
- [x] A disconnection mid-run closes the run rather than leaving it open indefinitely
- [x] The state machine is tested directly with a synthetic current-versus-time series
- [x] Test cases include: a clean insertion and withdrawal; a spike shorter than the
      debounce, which must not open a run; a current dwelling between the two thresholds,
      which must not close and reopen a run; a withdrawal shorter than the release
      interval, which must not close the run; force start below threshold; force stop
      above it; a disconnection mid-run
- [x] No test in this ticket sleeps

## Comments

### 2026-09-14 — Threshold-triggered acquisition runs with manual override implemented
1. Created `src/rbl/services/cup_acquisition.py`:
   - Pure Python state machine `CupAcquisitionStateMachine` and detector `CupDetector`.
   - Free of Qt imports and clock calls; timestamps are passed as explicit inputs.
   - ADR 0002 Decision 6 structural separation: `CupDetector` exposes `cup_in_beam` independently, so future position sensors can replace current-based inference without touching run lifecycle logic.
   - Hysteresis and debounce: 0.5 µA arm threshold (1.0 s debounce), 0.25 µA release threshold (3.0 s release interval).
   - Full support for `force_start`, `force_stop`, run ID incrementation, and disconnection handling.
2. Updated `src/rbl/gui/faraday_cup_tab.py`:
   - Added Acquisition Run panel with live status badge ("ACQUIRING (Run #N)", "In Beam (Arming)", "Idle", "Disconnected").
   - Added "Force Start" and "Force Stop" buttons to manually override detector.
   - Connected acquisition state transitions to dynamically switch picoammeter polling rate between 10 Hz (acquiring) and 2 Hz (idle).
3. Updated `src/rbl/snapshots.py` and `src/rbl/state/picoammeter_link.py`:
   - Added `cup_acquiring` tracking on `Beamline` / `PicoammeterLinkMixin` and extended `CupState` snapshot.
4. Added test suites:
   - `tests/test_cup_acquisition.py`: 15 deterministic unit tests covering debounce, hysteresis dwelling, withdrawal intervals, force start/stop overrides, and disconnection without sleeping.
   - `tests/test_faraday_cup_tab.py`: integration tests covering UI run indicators, button state lifecycle, threshold triggering through `CupFeed`, and disconnection cleanup.
5. All local quality gates passed: ruff, check_tests_first, type_gate (0 hard errors, 139 soft errors matching ratchet), and pytest (1715 passed, 0 failures).

