# 06: Expected-failure markers become strict

**What to build:** A muted test that starts passing becomes a build failure
instead of silence.

The strict setting for expected-failure markers is turned on repository-wide in
the pytest configuration, and no marker remains anywhere in the suite. From this
point, marking a test expected-to-fail in order to green a build is itself a
build failure. Expected-failure is not a tool for greening a build.

**Blocked by:** 05

**Status:** done

- [x] The pytest configuration sets strict expected-failure behaviour repository-wide.
- [x] A search of the test directory finds zero expected-failure markers.
- [x] Adding a marker to a passing test fails the build — demonstrated once in a CI run, then reverted.
- [x] The full suite is green in CI.

Reference: ADR 0001 decision 2.

## Comments

Verified 2026-09-08: `xfail_strict = true` added to `pytest.ini` (commit
`a9a09c0`). A repo-wide search (`grep -rn xfail`) confirmed zero
expected-failure markers remain anywhere under `tests/` before or after this
change.

Demonstration: commit `0d7818f` temporarily added
`@pytest.mark.xfail(reason="TEMPORARY: ticket 06 xfail_strict demonstration, reverted next commit")`
to `tests/test_raster_model.py::TestAgainstTheManualsWorkedExample::test_deflection_angle`,
a known-passing pure-math test with no fixtures or hardware dependency. Its
CI run (https://github.com/ilegault/RBL/actions/runs/34239363526) failed as
required: `FAILED tests/test_raster_model.py::TestAgainstTheManualsWorkedExample::test_deflection_angle
- [XPASS(strict)] TEMPORARY: ticket 06 xfail_strict demonstration, reverted next commit`,
`1 failed, 1686 passed in 118.59s`, exit code 1 — an unexpectedly-passing
xfail test failed the build exactly as ADR 0001 decision 2 requires. The
marker was then reverted in commit `916006b`
("Revert \"DEMO: mark a passing test xfail to prove xfail_strict fails the
build\""), whose CI run is green:
https://github.com/ilegault/RBL/actions/runs/34239848957.

No file under the application source tree was touched by this ticket — only
`pytest.ini` and the temporary/reverted test-file round-trip.
