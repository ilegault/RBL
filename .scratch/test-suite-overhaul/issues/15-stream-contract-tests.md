# 15: Stream contract tests over the declared tab list

**What to build:** Four rules, each stated once and run against every tab that
consumes stream windows, parametrized from the single tab declaration so that a
tab added later is covered without anyone remembering to add tests.

The rules:

1. A tab survives a window in which its channels are **absent** — the marker a
   paused readout produces. A paused readout must not blank or crash a screen.
2. A tab survives **non-finite** values. A disconnected instrument must not take
   down the interface.
3. A tab takes **only its own channels** from a shared window. One screen cannot
   consume another's data.
4. A tab **shuts down cleanly**. Closing the application with hardware
   disconnected must not hang.

Each is one test, parametrized over the declaration — not the same assertion
repeated per tab. The existing layering test is the template: one rule, checked
across every subject it binds. Windows are built through the sanctioned payload
helpers and pushed through a real beamline, not synthesised per tab.

These are written test-first: each is observed failing against a deliberately
broken tab before the suite is made green.

**Blocked by:** 12, 14

**Status:** done

- [x] Each of the four rules is one parametrized test, not a per-tab repetition.
- [x] The parametrization source is the tab declaration from ticket 12.
- [x] Adding a tab to the declaration extends all four tests without touching them, demonstrated once and reverted.
- [x] Windows are built through the shared payload helpers and pushed through a real beamline.
- [x] Each rule was observed failing before it passed, and the failure is described in this ticket's comments.
- [x] The suite is green in CI.

Reference: spec sections "Testing Decisions" and "Seams"; ADR 0001 decisions 1 and 5.

## Comments

### Implementation
- Created [`tests/test_stream_contracts.py`](file:///C:/Users/IGLeg/PycharmProjects/RBL/tests/test_stream_contracts.py) with four contract rules declared as single parametrized test methods over `STREAM_CONSUMING_DECLARATIONS` (derived from `TAB_DECLARATIONS` in [`src/rbl/gui/app.py`](file:///C:/Users/IGLeg/PycharmProjects/RBL/src/rbl/gui/app.py) where `consumes_stream=True`):
  1. `test_tab_survives_absent_channels`: Verifies each tab survives paused/empty readouts without crashing or blanking, and renders appropriate paused states.
  2. `test_tab_survives_non_finite_values`: Verifies each tab tolerates `NaN`, `+Inf`, `-Inf` inputs across all channels and redraws cleanly.
  3. `test_tab_takes_only_own_channels`: Enforces channel isolation (buffers and ingestion domains are strictly isolated and disjoint from other tabs' channels).
  4. `test_tab_shuts_down_cleanly`: Enforces clean, non-blocking, idempotent shutdown with all timers and runners stopped.
- Enhanced [`tests/payloads.py`](file:///C:/Users/IGLeg/PycharmProjects/RBL/tests/payloads.py) with `FULL_READING` and updated [`LabJackFeed`](file:///C:/Users/IGLeg/PycharmProjects/RBL/tests/payloads.py) to connect all stream lifecycle signals and emit `raw_window_ready` through a real `Beamline`.

### Test-First Observations
1. **Rule 1 (Absent Channels):** Injected a synthetic defect in `CurrentTab.on_logamp_state` to raise `KeyError` when channel is absent in paused mode. Observed `AssertionError: assert 'Waveform' in stream_tab.lbl_i['AIN0'].text()` as the paused indicator failed to render. Restored proper handling; all 5 stream tabs pass.
2. **Rule 2 (Non-Finite Values):** Injected a synthetic defect in `AmpTab._refresh_monitors` to raise `ValueError("Deliberate test-first failure: crashed on non-finite value in UI")` when encountering `math.isfinite(meas_kv) == False`. Observed `ValueError` during non-finite window ingestion. Restored graceful handling; all 5 stream tabs pass.
3. **Rule 3 (Channel Isolation):** Injected a synthetic defect into `CurrentTab.buffers` containing foreign channel `AIN6`. Observed `AssertionError: assert tab_buffer_keys.issubset(logamp_ains)` and `isdisjoint(amp_ains)`. Restored channel domain isolation; all 5 stream tabs pass.
4. **Rule 4 (Clean Shutdown):** Injected a synthetic defect into `CurrentTab.shutdown` to raise `RuntimeError`. Observed `RuntimeError: Deliberate test-first failure: shutdown crashed`. Restored clean shutdown; all 5 stream tabs pass.

### Parametrization Expansion Demo
- Added a temporary `TabDeclaration(title="Demo Stream Tab", attr_name="demo_stream_tab", factory=..., consumes_stream=True)` to `TAB_DECLARATIONS` in `src/rbl/gui/app.py`.
- Verified `pytest tests/test_stream_contracts.py --collect-only` automatically expanded from 20 to 24 test instances covering all four rules for the new tab without modifying `test_stream_contracts.py`.
- Reverted the demo addition.

### Test Suite Verification
- Ran full test suite: 1611 passed, 0 failed.

