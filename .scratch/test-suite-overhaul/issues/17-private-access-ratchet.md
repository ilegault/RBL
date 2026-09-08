# 17: Private-access ratchet

**What to build:** The count of private-attribute accesses in Qt-dependent test
modules can only go down.

A whole-codebase check counts them and compares against a stored figure. A rise
fails the build; a fall is expected to be committed alongside the change that
caused it. The check is built on the same machinery as the import-layering rule
and the GUI conversion ban — one rule, checked across every module — and the
stored figure is baselined **after** the deletions, rewrites and contract tests
have landed, not before, so it records the suite that exists rather than the one
being replaced.

This is what turns a written convention into an enforced one. The convention
already existed and was ignored roughly 870 times.

**Blocked by:** 14, 15, 16

**Status:** ready-for-agent

- [ ] A committed check counts private-attribute accesses in Qt-dependent test modules.
- [ ] The stored figure is the count measured after tickets 14, 15 and 16 landed.
- [ ] Adding a private access fails the build, demonstrated once in CI and reverted.
- [ ] Removing one and lowering the stored figure passes.
- [ ] The check is built on the existing whole-codebase rule machinery.

Reference: spec section "Cross-tab agreement" (same machinery) and user stories 45-46;
ADR 0001 Context.
