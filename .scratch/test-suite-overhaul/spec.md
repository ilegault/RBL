# Spec: Test suite overhaul — real tests, enforced, written first

Status: ready-for-agent
Date: 2026-09-08
Related: `CONTEXT.md`, `docs/adr/0001-tests-first-and-no-muted-failures.md`

> Read ADR 0001 before starting. It is binding, and it supersedes the test-handling
> instructions in `.claude/pending-plan.md` §7, which caused the problem this spec fixes.

## Problem Statement

The RBL test suite has 1,621 test functions and cannot be trusted.

CI has never run them. The workflow runs lint, then type check, then tests, in that
order; the type check exits non-zero, so the test step has never executed on any push.
The suite's green-ness is unverified everywhere except a developer's laptop.

Eleven tests are muted with a non-strict expected-failure marker. All eleven were
investigated and every one is a defect in the test, not in the application. The muting
was not accidental: a previous plan instructed an implementing model to mark every
failing test as expected-to-fail and write a one-line reason. Having no way to tell a
broken test from broken code, and no permitted move other than suppression, it invented
reasons. Three of them describe a calibration ladder step size that does not exist
anywhere in the codebase.

The suite is also large in the wrong places. It contains 951 private-attribute accesses
in Qt-dependent files, under a written convention that forbids exactly that. Only 5 of
80 test files use the sanctioned path for driving a widget with real data. The result is
a suite that breaks when the UI is rearranged and stays green when behaviour is wrong —
the opposite of what a test suite is for. Ten test modules import Qt without a guard and
abort collection for the entire run, so an environment without Qt yields zero tests
rather than a degraded subset.

Nothing anywhere asserts cross-tab agreement. Several tabs render the same device. Each
has tests poking its own widget; none compares two device views of one device. Two tabs
could disagree about an amplifier voltage by a factor of two and the suite would stay
green, because each passes its own tests. This application controls beamline hardware in
a laboratory where an operator reads a number off a screen and turns a knob.

Finally, the working order is backwards: the application is built and tests are written
afterwards, against whatever the code already does. A test written that way has never
been observed failing, so nobody knows whether it can fail.

## Solution

Three things, in order.

**Make the suite run and make it impossible to mute.** Fix the type-check configuration
so the test step executes. Fix all eleven muted tests — none is a production bug. Make
expected-failure markers strict, so a muted test that starts passing becomes a failure.
Give an implementing agent a legal alternative to suppression: stop, record what is
blocked, open a draft pull request, and escalate.

**Find out which existing tests are worth keeping.** Audit all 80 test files against
written criteria and produce a keep / rewrite / delete verdict for every test, with a
reason. Bloat is not a cosmetic problem here — it is what makes the suite unreadable
and hides the fact that the important invariants are untested.

**Rebuild around contract tests.** State each rule once and run it against every subject
the rule binds, rather than repeating an assertion per widget. A contract test covers the
next tab automatically; a per-widget test requires someone to remember. Add one
end-to-end session test through the main window, justified by the operational stakes.
From then on, tests are written first and observed failing before the code exists.

## User Stories

1. As a developer, I want CI to actually execute the test suite, so that a green check mark means the tests passed rather than that an earlier step failed first.
2. As a developer, I want the type checker's failures to be reported without blocking the test run, so that one class of problem does not hide another.
3. As a developer, I want every type error in the project reported in the CI log, so that nothing is skipped in secret.
4. As a developer, I want the gate that fails the build to be layered and written down in one visible place, so that a differing bar per layer is a stated standard rather than a hidden exception.
5. As a developer, I want type errors in the physics and instrument-protocol layers to fail the build immediately, so that the code producing numbers I publish is held to the highest bar.
6. As a developer, I want type errors elsewhere counted with a ratchet that can only decrease, so that the debt shrinks without blocking every push.
7. As a developer, I want no module excluded from the type checker by name, so that nobody can quietly remove a file from scrutiny.
8. As a developer, I want the lint configuration's blanket rule exemptions removed, so that unused variables in tests are reported rather than permitted.
9. As a developer, I want a run in an environment without Qt to yield the tests that can run rather than zero tests, so that a missing dependency degrades the run instead of erasing it.
10. As a developer, I want all eleven muted tests fixed rather than deleted, so that the behaviour they were meant to guard is actually guarded.
11. As a developer, I want the tests that assert on a state machine to advance it the way production advances it, so that they test the transition rather than the absence of a timer.
12. As a developer, I want the tests that assume all three pass types produce the same number of setpoints corrected to expect the lengths each type actually produces, so that the traversal shapes are pinned rather than conflated.
13. As a developer, I want the test that feeds a non-physical sentinel into a current-monitor channel to feed a physically possible value, so that it tests the discard behaviour rather than tripping the hard trip.
14. As a developer, I want the hard trip's behaviour on a railed monitor pinned by its own test, so that the interlock that fired correctly is documented as correct rather than recorded as a failure.
15. As a developer, I want expected-failure markers to be strict, so that a muted test which starts passing becomes a build failure instead of silence.
16. As a developer, I want a written rule that a failing test is fixed or escalated but never suppressed, so that the instruction that caused this cannot be reissued.
17. As an implementing agent, I want a legal move when I cannot fix a failure, so that suppression is not the only available action.
18. As an implementing agent, I want to commit finished work to a branch, record what blocked me, and open a draft pull request, so that the work is preserved and the blockage is visible without touching the main branch.
19. As a developer, I want the blocked report to live in the repository as a file on the branch, so that a planning session can read it directly without any tracker integration.
20. As a developer, I want the failing CI run attached to that draft pull request, so that the evidence and the report are in the same place.
21. As a developer, I want the stale plan file that instructed the muting deleted or archived, so that a fresh session reading the repository for context cannot follow it.
22. As a developer, I want every test file audited against written criteria, so that the verdict on each test is reproducible rather than a matter of taste.
23. As a developer, I want each audited test to carry a keep, rewrite, or delete verdict with a stated reason, so that I can disagree with a specific call rather than the whole exercise.
24. As a developer, I want tests that restate a library's own guarantee identified as bloat, so that the suite stops asserting that a stack widget sets the index it was told to set.
25. As a developer, I want tests that break on restructuring without any behaviour change identified as churn, so that I can see what my refactors are actually paying for.
26. As a developer, I want the audit to report which tests would have caught nothing, so that a smaller suite is a stronger one rather than a thinner one.
27. As a developer, I want the audit's output reviewed before any deletion happens, so that no test disappears without a decision.
28. As an operator, I want every screen that shows a given device to show the same value for it, so that acting on a number I read off one screen is safe.
29. As a developer, I want unit conversion to happen once, in the snapshot layer, so that two device views cannot drift apart by converting independently.
30. As a developer, I want a whole-codebase rule that forbids the GUI layer from calling conversion helpers, so that cross-tab agreement is structural rather than checked case by case.
31. As a developer, I want that rule enforced the same way the layering rule is enforced, so that it uses machinery the project already has.
32. As a developer, I want the existing tab that converts a monitor reading itself fixed to render a pre-converted value, so that the rule can pass.
33. As a developer, I want one contract test per rule rather than the same assertion repeated per tab, so that adding a tab does not require remembering to add tests.
34. As a developer, I want a contract test asserting every stream-consuming tab survives a window in which its channels are absent, so that a paused readout does not crash a screen.
35. As a developer, I want a contract test asserting every stream-consuming tab survives non-finite values, so that a disconnected instrument does not take down the interface.
36. As a developer, I want a contract test asserting each tab takes only its own channels from a shared window, so that one screen cannot consume another's data.
37. As a developer, I want a contract test asserting every tab shuts down cleanly, so that closing the application with hardware disconnected does not hang.
38. As a developer, I want the contract tests to iterate a single declared list of tabs, so that they cover tabs added after they were written.
39. As a developer, I want the main window to build its tabs from that same single declaration, so that the tests and the application cannot disagree about what the tabs are.
40. As a developer, I want the three parallel hand-ordered tab lists in the main window collapsed into one, so that a mis-ordered entry becomes impossible rather than merely unlikely.
41. As a developer, I want one end-to-end session test that builds the main window and drives a realistic run, so that a tab wired to nothing fails a test instead of showing an empty screen.
42. As a developer, I want the session test to assert on what an operator would see, so that it covers the rendering path rather than the internal state.
43. As a developer, I want per-tab tests kept only where the behaviour is genuinely unique to that tab, so that the suite stops paying for the same assertion twelve times.
44. As a developer, I want GUI tests that survive to drive widgets through the sanctioned data path, so that they exercise the production route rather than an imitation of it.
45. As a developer, I want a ratchet on the count of private-attribute accesses in tests, so that the number can only go down.
46. As a developer, I want the ratchet to fail the build when the count rises, so that the existing convention becomes enforced rather than merely written.
47. As a developer, I want new features implemented test-first, so that every test has been observed failing before its code existed.
48. As a developer, I want the implementing agent to run the tests in its own session, so that I never run the suite locally and still get the red-green cycle.
49. As a developer, I want to see results on GitHub rather than in a terminal, so that the state of the project is visible from anywhere.
50. As a developer, I want a CI check that fails a pull request touching application code with no corresponding test change, so that the tests-first rule is enforced rather than trusted.
51. As a developer, I want the vocabulary these rules use recorded in the domain glossary, so that a future session uses the same words for the same things.
52. As a developer, I want the reasoning behind these rules recorded as a decision record, so that the next model to hit a red build cannot reinvent the suppression plan.
53. As a researcher, I want the tests covering the physics that reaches my publications left intact, so that the audit reduces bloat without touching the numbers I rely on.

## Implementation Decisions

### Ordering

Three phases, and they are not independent. The audit cannot be scoped until the suite
demonstrably runs, and the contract tests cannot be written until the audit says which
existing tests they replace. `to-tickets` must carry these as blocking edges.

1. Make the suite run and close the suppression routes.
2. Audit every test file and produce verdicts.
3. Delete, rewrite, and build the contract layer on the audit's output.

### CI and gating

The workflow keeps running lint, type check, and tests, but the test step no longer sits
behind a type-check failure. Type errors are reported in full; only some of them fail the
build.

The type checker covers all application source with **no exclusions by module name**. The
current exclusion list is removed. Its former entries fail through follow-on imports
anyway, which is why the exclusions never worked — the exclusion mechanism governs
directory discovery, not modules pulled in by other checked modules. The correct mechanism
is a per-module override, and the per-layer bar is expressed that way, in one visible
block.

Layer bars: the configuration and hardware layers fail the build on any error. The state,
services, and GUI layers are reported and counted, with the count held by a ratchet that
may decrease and never increase.

The linter's blanket exemptions are removed on the same terms. The unused-variable rule is
re-enabled for tests immediately — it is the rule that would have caught the dead
assignment in the tab-persistence test that pretends to simulate a hidden tab.

Expected-failure markers become strict repository-wide, so a marker on a passing test is a
failure. `xfail` is not a tool for greening a build.

### Qt as a hard test dependency

Qt is declared in the development requirements and is present wherever the suite runs — CI
and the implementing agent's session both install it. The import guards scattered through
the suite therefore serve no purpose, and the ten unguarded modules that abort collection
are a bug, not a convention. Guards are removed and Qt is treated as required. A missing
dependency is an environment failure that should be loud.

### The eleven muted tests

All eleven are test defects. No production change is required by any of them, and none may
be deleted.

- Six assert on a calibration state machine immediately after starting it, before its
  settle period has elapsed. Production enters a settle state first and only reaches
  collection when the settle handler runs. The tests must advance the machine the way
  production advances it, which the calibration test module's own docstring already
  describes.
- Four assume the three pass types produce equal-length sequences. They do not, by design:
  `up` and `down` each visit every rung twice, out and back on each polarity, while
  `random` visits each rung once. The expectations must be corrected per pass type. The
  reason strings on these markers reference a finer inner-ladder step that exists nowhere
  in the codebase; that claim is false and must not be carried forward.
- One feeds a 999 V sentinel into every amplifier channel, including the current monitors.
  At the documented monitor gain that is a 9990 mA reading and the hard trip aborts,
  correctly. The fixture must use a physically possible value, and it must stop writing
  the same value to voltage-monitor and current-monitor channels indiscriminately. The
  hard trip's behaviour on a railed monitor gains its own test asserting that it fires.

### Blocked-work protocol

When an implementing agent cannot fix a failure it stops. It commits finished work to the
branch, sets the ticket's own `Status:` line to `blocked`, and appends what was attempted,
what failed, and what needs deciding under the ticket's `## Comments` heading, following
the convention in the issue-tracker document. It then opens the pull request as a
**draft**. The main branch is not touched. The ticket file travels with the branch, so the
draft pull request and its failing CI run are the report, and a planning session reads the
ticket directly.

The report must land in a **tracked** directory. The agent working directory is gitignored
in this repository: anything written there is never pushed and no reviewer ever sees it.
The tracker directory is not ignored, which is why the ticket file itself is the report.

The stale plan file containing the original suppression instruction is deleted. Being
gitignored, it exists only on the developer's disk, which makes it invisible to review and
readable by any fresh local session — it must be removed rather than left to be found.

### The audit

Every test file is audited and every test receives a verdict of keep, rewrite, or delete
with a stated reason. Verdicts are written to the tracker as reviewable output; **no test
is deleted before the verdicts are reviewed.**

Criteria, in the vocabulary of ADR 0001:

- **Delete** if the test is a *churn test*: it fails when code is restructured without
  behaviour changing and would not have caught a real defect. Also delete if it restates a
  guarantee the framework already makes, or if it writes the private state it then reads
  back.
- **Rewrite** if it covers real behaviour but reaches it by calling a private method or
  reading a private attribute. Rewrites drive the widget through the sanctioned data path.
- **Keep** if it drives the production path and asserts on something an operator could see
  or a downstream consumer reads, or if it tests hardware-layer mathematics directly.

The hardware layer's mathematical tests are presumed keepers. They pin formulas against
the instrument manual's worked example and against the laboratory's own deflection sheet,
and they invert a planted beam to recover it. The audit confirms rather than re-litigates
them.

### Cross-tab agreement

Enforced **structurally**. A whole-codebase rule forbids the GUI layer from calling unit
conversion helpers; conversion happens once, in the snapshot layer, and tabs render what
they are handed. This is checked by the same machinery that enforces the layering rule and
adds no new seam.

The rule fails on day one: one amplifier tab converts a raw monitor reading to kilovolts
itself. Fixing that is part of this work, not a follow-up.

A behavioural pairwise comparison of device views is added **only** for devices the audit
finds rendered on more than one tab, and only if the structural rule proves insufficient
for them. It is not built speculatively.

> Decision taken without an explicit answer from the developer, flagged here: the
> structural route was recommended and not objected to. Reversing it means adding a
> per-tab declaration of rendered devices, which is a contained change.

### The single new seam

The main window currently writes its tab list three times — the tab-bar labels, the
constructor assignments, and the stack insertion order — as three parallel sequences kept
in the same order by hand. These collapse into **one ordered declaration** that the main
window builds from and the contract tests iterate.

This is the only new seam. It carries its weight as a refactor independent of testing: it
removes a three-way ordering hazard of exactly the kind that left a hardcoded tab-index
table in the test suite describing tabs that no longer occupy those positions.

Existing seams are preferred everywhere else, and no others are added.

## Testing Decisions

### What makes a good test here

A test asserts external behaviour: what an operator sees, what a downstream consumer
reads, or what a pure function returns. It does not assert on private attributes, private
method calls, or widget structure. It must be able to fail — for new work, it is observed
failing before its code exists.

A test that fails when code is restructured and behaviour does not change is a churn test
and is a liability, because its cost is paid on every refactor and its benefit is zero.

### Seams

- **Stream data into widgets**: the existing test payload helpers, which build a real
  stream window and push it through a real beamline into real tabs. This is the sanctioned
  path and the convention already names it; it is currently used by 5 of 80 files.
- **Tabs that consume stream windows**: the main window already holds this set as a
  member. It is the parametrization source for stream contract tests and requires no new
  code.
- **Tab lookup**: the main window's title-based index lookup, in place of hardcoded
  indices, so that reordering tabs cannot silently invalidate a test.
- **Whole-codebase rules**: the existing layering-check script and its test. Both new
  enforcement rules — the GUI conversion ban and the private-access ratchet — are built on
  this, not on new machinery.
- **Hardware mathematics**: direct function calls. No seam required.
- **The whole application**: the main window, for the single end-to-end session test.

### Prior art in this repository

Three existing tests are the models to imitate, and implementers should read them first:

- The layering test, which states one rule and checks it across every module. This is the
  template for all contract and enforcement tests.
- The one-window-reaches-both-tabs test in the GUI hardware module, which pushes a real
  stream window through the application's own beamline and asserts on rendered label text.
  This is the template for GUI contract tests.
- The raster-model tests, which pin formulas to the steerer manual's worked example and to
  the laboratory deflection sheet, and include a test whose stated purpose is to fail if a
  removed geometric correction is ever reintroduced. This is the standard for
  hardware-layer tests.

### Modules under test

Contract tests bind every tab in the declared list. The end-to-end session test binds the
main window. The enforcement rules bind the whole source tree. The audit covers all 80
test files.

## Out of Scope

- **Mutation testing.** Considered and declined as too slow to be worth its setup here.
  The consequence is recorded in ADR 0001: nothing will prove the hardware-layer tests
  would notice a subtly wrong formula. Do not add it under this spec.
- **Annotating the GUI layer for the type checker.** The layered gate exists precisely so
  that this remains a separate, separately funded effort.
- **Rewriting the hardware-layer tests.** They are the strongest tests in the repository.
  The audit confirms them; it does not restructure them.
- **Behaviour changes to the application**, except the two the rules require: rendering a
  pre-converted value in the amplifier tab, and building tabs from the single declaration.
  No feature work.
- **Resolving the "drift" naming collision** recorded in the glossary. Documented, not
  renamed.
- **Raising the strictness bar for the state, services, and GUI layers.** Only the ratchet
  applies.
- **Adding a pre-commit framework or Claude Code hooks.** Enforcement is in CI, where it
  binds every author rather than only agent sessions.

## Further Notes

The suite has never been observed passing anywhere except a developer's laptop. The first
ticket must establish that baseline before any test is changed, and the result is recorded
in the tracker. Every later ticket is guesswork until it exists.

The counts in this spec — 1,621 tests, 951 private accesses in Qt files, 5 of 80 files on
the sanctioned path, 817 Qt-dependent tests — are from 2026-09-08 at commit `058a997`.
Treat them as the baseline to measure against, not as facts to preserve. A substantially
smaller test count at the end of this work is the intended outcome, not a regression.

The developer does not run the suite locally and does not intend to. Every ticket must be
verifiable from CI output alone.

The tab-index table in the GUI hardware test module documents four tabs; the application
has twelve and opens on one that is not in the list. It is a small thing and a fair
summary of the problem: the tests describe an application that no longer exists.
