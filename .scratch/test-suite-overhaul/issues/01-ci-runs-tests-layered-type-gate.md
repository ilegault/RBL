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

**Status:** done

- [x] A CI run on a push shows the test step executing and reporting a test count.
- [x] The type checker reports errors from every module under the application source root; no module is excluded by name.
- [x] An introduced type error in the configuration or hardware layer fails the build.
- [x] An introduced type error in the state, services or GUI layer is printed, raises the counted total, and fails the build only because the count rose above the stored figure.
- [x] The stored figure lives in a tracked file, and the check comparing against it is committed.
- [x] The baseline counts are recorded as a file under `.scratch/test-suite-overhaul/`.
- [x] No file under `tests/` is modified by this ticket.

Reference: spec sections "CI and gating" and "Further Notes"; ADR 0001 decision 4.

## Comments

This ticket merged as PR #27 with the gate it built still failing on master
(hard-layer errors + a wrong ratchet), and `.scratch/test-suite-overhaul/baseline.md`
was not written at merge time even though the checklist called for it. Both are
fixed in a follow-up commit on this branch:

- **Baseline recorded after the fact.** `baseline.md` was authored from PR #27's
  own CI logs (the `pull_request` run and the post-merge `push` run on master,
  both at commit range around `e9ecbab`/`c689336`, 2026-09-08 04:23–04:32Z,
  `windows-latest`, both matrix jobs) rather than from a fresh run, since the
  ticket's own acceptance criterion is "the first run in which the tests
  actually execute" and that run already happened on PR #27.
- **Hard-layer errors.** Of the 12 hard-layer errors that run reported, 10 are
  genuine stub gaps (`pyvisa`'s `Resource` and `cv2`'s module surface don't
  declare the attributes `funcgen_driver.py` and `camera_source.py` use) — a
  `[[tool.mypy.overrides]]` block now disables only `attr-defined` for exactly
  those two modules, so every other error class in either module is still
  reported and still fails the build. The remaining 2 were real bugs, fixed
  directly: `load_model.py:135` (an `_ArrayOrScalarCommon`/`float` operand
  mismatch from an unconverted `np.trapezoid` result) and `camera_source.py:135`
  (`_latest_frame` had no type annotation, so mypy inferred it as `None`-only
  and rejected every frame assigned to it) — not a stub gap, so it was annotated
  rather than folded into the override.
- **Ratchet.** `tools/mypy_ratchet.txt` said 132; the CI run actually measured
  140 soft-layer errors. Corrected to 140, taken from that same PR #27 run
  (https://github.com/ilegault/RBL/actions/runs/34187056116, jobs
  `test (3.13)` / `test (3.14)`), not from a local run.
