# 05: The eleven muted tests are fixed

**What to build:** Every test currently carrying a non-strict expected-failure
marker asserts the behaviour it was written to guard, and passes with its marker
gone.

All eleven were investigated and **every one is a defect in the test, not in the
application**. No production change is required by any of them, and **none may
be deleted**. They fall into three groups.

*Six* assert on the calibration state machine immediately after starting it,
before its settle period has elapsed. Production enters a settle state first and
only reaches collection when the settle handler runs. The tests must advance the
machine the way production advances it, which the calibration test module's own
docstring already describes. Advancing it is the fix — not patching the timer
away, and not asserting a different state.

*Four* assume the three calibration pass types produce equal-length setpoint
sequences. They do not, by design: the up and down passes each visit every rung
twice, out and back on each polarity, while the random pass visits each rung
once. Correct the expectation per pass type, derived from the traversal the code
actually performs. The reason strings on these markers reference a finer
inner-ladder step size that exists nowhere in the codebase; that claim is false
and must not be carried forward into the corrected expectations.

*One* feeds a 999 V sentinel into every amplifier channel, including the current
monitors. At the documented monitor gain that is a 9990 mA reading, and the hard
trip aborts — correctly. The fixture must use a physically possible value, and
must stop writing the same value indiscriminately to voltage-monitor and
current-monitor channels. The hard trip's behaviour on a railed monitor gains
**its own new test** asserting that it fires: the interlock did its job and
deserves to be pinned rather than recorded as a failure.

**Blocked by:** 01

**Status:** done

- [x] All eleven expected-failure markers are gone and all eleven tests pass in CI.
- [x] The six state-machine tests reach the collection state by letting the settle handler run.
- [x] No state-machine test patches the settle timer away or substitutes an assertion on a different state.
- [x] The four pass-type tests assert a distinct expected length per pass type.
- [x] No corrected expectation references an inner-ladder step size that does not appear in the configuration.
- [x] The sentinel fixture drives voltage-monitor and current-monitor channels with separately chosen, physically possible values.
- [x] A new test asserts the hard trip fires when a current monitor reads at rail, and it was observed failing before the code path made it pass.
- [x] No file under the application source tree is modified by this ticket.

## Comments

Verified 2026-09-08: implemented in commit `e9ede22` ("Fix the eleven muted
calibration tests (ticket 05)"), merged to master via PR #29
(`cbc0d17`). Confirmed: `tests/test_calibration_config.py` and
`tests/test_calibration_runner.py` carry no remaining `xfail` markers; the
diff touches only those two test files, no application source; PR #29's
final CI run (workflow run 34236368603) is green on both Python 3.13 and
3.14.

Reference: spec section "The eleven muted tests"; ADR 0001 Context and decision 1.
