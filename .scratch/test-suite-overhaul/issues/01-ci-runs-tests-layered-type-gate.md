# 01: CI executes the test suite and the type gate is layered

**What to build:** A push produces a CI run in which the test step actually
executes, and the build's pass/fail reflects what the tests did. Today the type
check runs before the tests and exits non-zero, so the test step has never run
on any push and the suite's green-ness is unverified everywhere except a
developer's laptop.

The type checker covers all application source with **no module excluded by
name**. Errors in the configuration and hardware layers fail the build. Errors
in the state, services and GUI layers are printed in full and counted, and the
count is held against a stored figure that may decrease and may never increase.
The per-layer bar is expressed in one visible configuration block, as per-module
overrides rather than a discovery-level exclusion list — the current exclusions
never worked, because the excluded modules are pulled in by other checked
modules anyway. The exclusion mechanism governs directory discovery, not modules
reached through imports.

The result of the first run in which the tests actually execute is recorded in
the tracker as the project's **baseline**: collected, passed, failed, errored.
Every later ticket measures against it, and until it exists every later ticket
is guesswork. No test is changed in this ticket.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] A CI run on a push shows the test step executing and reporting a test count.
- [ ] The type checker reports errors from every module under the application source root; no module is excluded by name.
- [ ] An introduced type error in the configuration or hardware layer fails the build.
- [ ] An introduced type error in the state, services or GUI layer is printed, raises the counted total, and fails the build only because the count rose above the stored figure.
- [ ] The stored figure lives in a tracked file, and the check comparing against it is committed.
- [ ] The baseline counts are recorded as a file under `.scratch/test-suite-overhaul/`.
- [ ] No file under `tests/` is modified by this ticket.

Reference: spec sections "CI and gating" and "Further Notes"; ADR 0001 decision 4.
