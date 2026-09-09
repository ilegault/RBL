# 16: End-to-end session test through the main window

**What to build:** One test that builds the whole main window, drives a
realistic run through it, and asserts on what an operator would see.

It constructs the window with no hardware attached, pushes stream windows,
vacuum readings and scope data through the real beamline the way the application
does, and then reads rendered text off the screens — not internal state. A tab
wired to nothing must fail this test rather than show an empty screen and pass
its own unit tests.

Its justification is operational, not stylistic: this application controls
beamline hardware in a laboratory where an operator reads a number off a screen
and turns a knob. One test that exercises the whole wiring path is the cheapest
insurance that the wiring exists.

Written test-first: observed failing — by disconnecting one tab's wiring — before
it is made to pass.

**Blocked by:** 12, 14

**Status:** done

- [x] One test builds the real main window and drives a run through the real beamline.
- [x] Its assertions read rendered text an operator would see, not internal widget state.
- [x] Disconnecting a single tab's wiring makes it fail, demonstrated once and reverted.
- [x] It runs with no hardware attached.
- [x] The suite is green in CI.

Reference: spec sections "Solution" and "Modules under test"; ADR 0001 decision 1.

## Comments

- Implemented `test_end_to_end_session_through_main_window` in [`tests/test_e2e_session.py`](file:///C:/Users/IGLeg/PycharmProjects/RBL/tests/test_e2e_session.py).
- Constructs the full, real `MainWindow` headless with no physical hardware attached.
- Drives a realistic beamline run through the real pipelines:
  1. Stepper Motors 4-axis poll state (`A`, `B`, `C`, `D`).
  2. Dual DG1022Z Function Generator differential drive readbacks (X at 250 Hz, Y at 100 Hz).
  3. Vacuum state ingestion (XGS-600 chamber gauge and VGC083 beamline gauge) and active interlock designation.
  4. LabJack stream ingestion via `window_payload` with log amp voltages (3.0 V -> 1 µA) and HV amplifier monitors (3.0 kV, 10 mA with anti-phase waveforms).
  5. Tektronix Scope / Profiler waveform state with multi-peak Gaussian fit resolving X and Y FWHM widths.
- Asserts strictly on operator-visible text across Overview, Beam Current, HV Amplifiers, Stepper Motors, Vacuum, and Beam Profiler tabs without inspecting private variables.
- Demonstrated test-first discipline:
  - Disconnected `self.beamline.logamps_changed.connect(self.current_tab.on_logamp_state)` in `src/rbl/gui/app.py` -> verified test failed (`AssertionError: assert '3.00' in '—'`).
  - Disconnected `self.beamline.amps_changed.connect(self.amp_tab.on_amp_state)` in `src/rbl/gui/app.py` -> verified test failed (`AssertionError: assert '3.00' in '—'`).
  - Reverted disconnects and restored passing state.
- Hardened `_max_live_commanded_kv` in [`src/rbl/state/hv_interlock_link.py`](file:///C:/Users/IGLeg/PycharmProjects/RBL/src/rbl/state/hv_interlock_link.py) to validate dictionary and numeric types when reading live generator output states.
- Verified entire test suite with pytest (1,611 passed, 0 failures, 0 errors).
