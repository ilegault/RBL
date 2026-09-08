# 03: Lint blanket exemptions removed

**What to build:** The linter reports the violations it was configured to
ignore, and the repository is clean against the fuller rule set.

The blanket rule ignores at the top level and the per-file exemption block
covering the whole test directory are removed. The unused-variable rule is
re-enabled for tests immediately — it is the rule that would have caught the
dead assignment in the tab-persistence test, which pretends to simulate a hidden
tab and then asserts nothing about it. Every violation the removal surfaces is
fixed rather than re-exempted. An exemption that survives does so on a single
named file for a stated reason, never on a directory glob.

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] The top-level ignore list and the whole-directory test exemption block no longer carry blanket entries.
- [ ] The lint step passes in CI with the reduced exemptions.
- [ ] The dead assignment in the tab-persistence test is either removed or turned into a real assertion.
- [ ] Every remaining exemption names one file and carries a one-line reason.
- [ ] The test count in CI is unchanged from the ticket 01 baseline.

Reference: spec section "CI and gating"; ADR 0001 decision 4.
