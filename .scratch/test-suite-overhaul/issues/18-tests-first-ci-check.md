# 18: A pull request that changes application code with no test change fails

**What to build:** The tests-first rule becomes enforced rather than trusted.

A CI check fails a pull request whose diff touches the application source tree
and contains no change under the test directory. It runs in CI, where it binds
every author, rather than in a local hook that binds only some sessions.

It lands last, after the rebuild, so that it does not fight the source-only
refactors this effort already ticketed. Give it one documented escape: a labelled
or explicitly annotated pull request that states why no test change applies —
visible in review, not silent.

**Blocked by:** 15, 16

**Status:** done

- [x] A pull request touching application source with no test change fails the check.
- [x] A pull request touching application source with a test change passes.
- [x] A test-only or documentation-only pull request passes.
- [x] The escape is explicit and visible on the pull request, and is documented alongside the blocked-work protocol from ticket 02.

Reference: spec user story 50; ADR 0001 decision 1.

## Comments

- Built [`scripts/check_tests_first.py`](file:///C:/Users/IGLeg/PycharmProjects/RBL/scripts/check_tests_first.py) to enforce the tests-first rule in CI across pull requests and commits.
  - Scans diffs / changed files for application source changes (`src/`) vs test changes (`tests/`).
  - Fails if `src/` is modified without changes under `tests/`.
  - Passes if both `src/` and `tests/` are modified, or if only non-application files (`tests/`, `docs/`, `scripts/`, `.github/`, config files) are modified.
  - Implements visible escape mechanisms: PR labels (`tests-exempt`, `skip-test-gate`), commit message / PR text annotations (`[no-test-needed: <reason>]`, `[tests-exempt: <reason>]`, `[skip-test-gate]`), or CLI arguments (`--exempt-reason`).
- Added full unit test coverage in [`tests/test_check_tests_first.py`](file:///C:/Users/IGLeg/PycharmProjects/RBL/tests/test_check_tests_first.py) (11 unit tests covering file categorization, pass/fail scenarios, escape parsing, GitHub event payload parsing, and CLI invocations).
- Updated CI workflow [`.github/workflows/tests.yml`](file:///C:/Users/IGLeg/PycharmProjects/RBL/.github/workflows/tests.yml) to fetch history (`fetch-depth: 0`) and run `python scripts/check_tests_first.py`.
- Documented the CI gate and escape mechanism in [`docs/adr/0001-tests-first-and-no-muted-failures.md`](file:///C:/Users/IGLeg/PycharmProjects/RBL/docs/adr/0001-tests-first-and-no-muted-failures.md) (Decision 1) and in [`CLAUDE.md`](file:///C:/Users/IGLeg/PycharmProjects/RBL/CLAUDE.md) §11.
- All 1,626 tests in the test suite pass cleanly.
