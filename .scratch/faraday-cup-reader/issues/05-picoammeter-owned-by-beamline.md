# 05: Picoammeter owned by Beamline

**What to build:** The picoammeter becomes an instrument the application owns, in the
same way it owns every other instrument. Connecting it from Connect All brings it up;
polling it produces a typed cup current snapshot that anything can subscribe to.

No user-visible screen in this ticket. What this delivers is the complete path from
bytes on the wire to a published snapshot, verifiable on its own through the test seam.

**Blocked by:** 04

**Status:** done

The test seam introduced here is the one the rest of the feature is tested through, so
it matters more than the usual test helper. A cup feed helper joins the existing test
payload module alongside the LabJack feed, injecting **raw SCPI response strings** into
a real `Beamline` wired as the main window wires it. Follow the LabJack feed's shape and
read its docstring first: the previous approach fed a widget's private method directly
and so tested a widget-shaped imitation of the production path instead of the path.

- [x] A polling worker performs all blocking instrument I/O on its own thread and
      communicates only by signals
- [x] The worker owns its instrument handle for its lifetime
- [x] A link mixin joins `Beamline`, following the shape of the existing vacuum link
- [x] `Beamline` owns the instrument — no widget constructs or tears one down
- [x] Cup current is published as a frozen snapshot carrying `connected`, like every
      other snapshot
- [x] The snapshot carries current, instrument timestamp, status word, and an over-range
      flag
- [x] No unit conversion happens anywhere in this path — the instrument returns amps
      already scaled, and that must remain true
- [x] The picoammeter participates in the stepped Connect All sequence without making
      the window appear hung
- [x] Connect and disconnect are available independently of the rest of the beamline
- [x] Teardown is ordered correctly in shutdown and leaves the instrument's state alone
- [x] Idle polling runs at 2 Hz; both poll rates live in the config layer, not as
      literals
- [x] A cup feed helper exists in the test payload module and injects raw SCPI strings
      into a real `Beamline`
- [x] A test drives that helper and asserts on the snapshot `Beamline` publishes,
      including an over-range sample
- [x] Nothing connected produces a snapshot with `connected` false, not a zeroed reading

## Comments

### 2026-09-14 — Implemented Keithley 6482 ownership in Beamline
- Created `PicoammeterWorker` QThread for non-blocking I/O polling Keithley 6482 at configurable 2 Hz idle / 10 Hz acquiring rates.
- Created `PicoammeterLinkMixin` mixed into `Beamline` managing driver lifecycle and publishing frozen `CupState` snapshots.
- Added `CupFeed` to `tests/payloads.py` for inject-raw test seam testing.
- Added `"Faraday cup"` step to `MainWindow._connect_all_steps()`.
- Verified all quality gates and test suites pass.

