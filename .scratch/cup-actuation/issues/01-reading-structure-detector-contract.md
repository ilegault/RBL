# 01: One reading structure for the cup detector seam

**What to build:** A refactor with no behaviour change. Today `CupDetector.update` and
`CupAcquisitionStateMachine.update` take a current reading as loose positional arguments
(`current`, `t`, `over_range`, `connected`). A position detector cannot satisfy that
signature — it has no current and no over-range flag — so the two would have to be told
apart inside the state machine, which is exactly what ADR 0003 decision 5 forbids.

Widen the contract to one frozen reading structure that carries everything either
detector needs, so a position detector can be dropped in later at its source with the
state machine untouched.

This ticket also lands the regression test that documents *why* the position path is
being built: the existing current-inference path, fed a one-second insertion, opens no
run at all.

**Blocked by:** None (can start immediately)

**Status:** done

**Read `docs/adr/0003-commanded-and-confirmed-cup-position.md` first**, decision 5 in
particular. `docs/adr/0001-tests-first-and-no-muted-failures.md` is binding.

Structural requirements, stated as requirements because an implementer will read them
as preferences otherwise:

- The reading structure is a **frozen dataclass in `rbl/services/cup_acquisition.py`**.
  It is an input to `update`, not something the detector constructs.
- `CupAcquisitionStateMachine` must not gain any field, branch, or attribute check that
  depends on which detector it holds. It sees one boolean out of the detector and
  nothing else. This is the seam; a `hasattr` or an `isinstance` in the state machine
  is a failed ticket.
- Every field a future position detector needs may be present and unset. Fields the
  current detector ignores are ignored silently, not asserted against.

- [x] A frozen reading dataclass exists carrying, at minimum: timestamp, measured
      current (nullable), over-range flag, and connected flag
- [x] `CupDetector.update` takes that structure and returns `cup_in_beam` exactly as now
- [x] `CupAcquisitionStateMachine.update` takes that structure; its public behaviour,
      return types (`RunOpened` / `RunClosed` / `None`), run-id sequencing, force
      start/stop and disconnect handling are unchanged
- [x] `CupAcquisitionStateMachine` contains no reference to any detector subclass, no
      `hasattr`, and no `isinstance`
- [x] Every existing caller is updated: `rbl/state/picoammeter_link.py`,
      `rbl/gui/faraday_cup_tab.py`, and every test that constructs either object
- [x] The full existing `tests/test_cup_acquisition.py` suite passes unchanged in
      meaning — assertions are rewritten to the new call shape, never weakened
- [x] A new regression test feeds the current-inference path a one-second insertion
      (current above `CUP_ARM_THRESHOLD_A` for 1.0 s, then baseline) and asserts that
      **no run opens**, with a comment naming ADR 0003 as the reason this limitation is
      documented rather than fixed here
- [x] A second regression test feeds a three-second insertion and asserts the run opens
      ~1.0 s after insertion and closes ~3.0 s after withdrawal, so the tail-of-baseline
      defect is pinned by a test rather than by prose
- [x] Module docstrings in `cup_acquisition.py` updated to explain the widened contract
      and why it is wider than the current detector needs
- [x] No test in this ticket sleeps
- [x] `ruff check .`, `python scripts/check_tests_first.py`, `python tools/type_gate.py`
      and `pytest --tb=short -q -n auto --dist loadfile` all pass

## Comments

- 2026-09-15: Completed ticket 01. Introduced frozen `CupReading` dataclass in `rbl.services.cup_acquisition` with timestamp (`t` and alias `timestamp`), nullable `current`, `over_range`, `connected`, `confirmed_position`, and `auto_mode`. Updated `CupDetector.update` and `CupAcquisitionStateMachine.update` to accept `CupReading` without any detector-subclass checks, `hasattr`, or `isinstance`. Updated `faraday_cup_tab.py` and test suite `tests/test_cup_acquisition.py` to the new calling convention. Added two regression tests in `test_cup_acquisition.py` pinning the 1-second insertion no-open limitation and the 3-second insertion tail-of-baseline defect per ADR 0003. Verified all 4 local gate checks pass with 0 failures (1,738 tests passing).

