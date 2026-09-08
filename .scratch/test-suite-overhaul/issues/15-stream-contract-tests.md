# 15: Stream contract tests over the declared tab list

**What to build:** Four rules, each stated once and run against every tab that
consumes stream windows, parametrized from the single tab declaration so that a
tab added later is covered without anyone remembering to add tests.

The rules:

1. A tab survives a window in which its channels are **absent** — the marker a
   paused readout produces. A paused readout must not blank or crash a screen.
2. A tab survives **non-finite** values. A disconnected instrument must not take
   down the interface.
3. A tab takes **only its own channels** from a shared window. One screen cannot
   consume another's data.
4. A tab **shuts down cleanly**. Closing the application with hardware
   disconnected must not hang.

Each is one test, parametrized over the declaration — not the same assertion
repeated per tab. The existing layering test is the template: one rule, checked
across every subject it binds. Windows are built through the sanctioned payload
helpers and pushed through a real beamline, not synthesised per tab.

These are written test-first: each is observed failing against a deliberately
broken tab before the suite is made green.

**Blocked by:** 12, 14

**Status:** ready-for-agent

- [ ] Each of the four rules is one parametrized test, not a per-tab repetition.
- [ ] The parametrization source is the tab declaration from ticket 12.
- [ ] Adding a tab to the declaration extends all four tests without touching them, demonstrated once and reverted.
- [ ] Windows are built through the shared payload helpers and pushed through a real beamline.
- [ ] Each rule was observed failing before it passed, and the failure is described in this ticket's comments.
- [ ] The suite is green in CI.

Reference: spec sections "Testing Decisions" and "Seams"; ADR 0001 decisions 1 and 5.
