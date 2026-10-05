# 04: One setter for the thresholds, so the recorded value is the compared value

**Blocked by:** None (can start immediately)

**Status:** in-progress

**Read first:** `src/rbl/services/cup_acquisition.py` in full, in particular
`CupDetector.__init__`, `CupDetector.update`, `CupPositionDetector`,
`AuthorityDetector.__init__`, and the four places `CupAcquisitionStateMachine` reads
`self.detector.arm_threshold`. `docs/adr/0002-...md` decision 4 (every run records the
thresholds in force when it began).

**What to build:** A single way to change the acquisition thresholds at runtime, such
that the number a run records is the number the detector actually compared the current
against. Today those can be two different numbers: `AuthorityDetector.__init__` calls
`super().__init__()`, so the composite carries the config defaults on itself, while
the `CupDetector` in `self._inference` carries its own copy and is the one that does
the comparing. `CupAcquisitionStateMachine` reads the composite's attributes. Nothing
edits thresholds today, so the two agree by accident; the moment anything edits one,
the session file starts lying about why a run opened.

- [ ] `CupDetector.set_thresholds(arm_threshold: float, release_threshold: float)`
      validates that the release threshold is strictly below the arm threshold, raising
      the same `ValueError` with the same message text `__init__` already raises, then
      assigns both attributes. A test asserts the raise and that a valid call changes
      both attributes.
- [ ] `AuthorityDetector.set_thresholds` overrides it so that one call sets the
      thresholds on `self._inference`, on `self._position`, and on the inherited
      attributes the state machine reads. A test asserts all three report the new
      values after one call.
- [ ] `CupPositionDetector.arm_threshold` and `release_threshold` become instance
      attributes assigned in `__init__` from the `cup_config` constants and updated by
      `set_thresholds`, instead of the class attributes they are today. A test asserts
      that setting them on one instance leaves a second instance unchanged.
- [ ] One test proves the defect is gone, asserting both halves together: build an
      `AuthorityDetector` and a `CupAcquisitionStateMachine`, call `set_thresholds`
      with an arm threshold **below** the current default, feed `CupReading`s carrying
      a current that is above the new arm threshold and below the old one for longer
      than `CUP_ARM_DEBOUNCE_S`, and assert that a `RunOpened` is returned **and** that
      its `arm_threshold` equals the new value. Both assertions in one test: the
      failure mode is precisely the two disagreeing.
- [ ] `CupAcquisitionStateMachine` is not modified. Not one line. It keeps reading
      `self.detector.arm_threshold` and `self.detector.release_threshold`. If it
      appears to need a change, ticket 01 of `.scratch/cup-actuation/` got the detector
      contract wrong, and that is an escalation under ADR 0001, not a workaround.

**Tests may fake:** nothing. `CupReading` is a plain value; build them directly. No
Qt, no hardware, no filesystem.

**Out of scope:** where the new threshold values come from (tickets 11 and 12), the
settle window (ticket 06), and any change to the authority rule or to how
cup-in-beam is decided.
