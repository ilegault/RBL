# ADR 0001 — Tests are written first, and failures are never muted

- **Status:** Accepted
- **Date:** 2026-09-08
- **Supersedes:** the test-handling instructions in `.claude/pending-plan.md` §7

## Context

On 2026-09-03 the suite held 1,621 test functions and CI was red. Investigation
found the following, and the causes matter more than the counts:

**CI had never run the tests.** `.github/workflows/tests.yml` runs ruff → mypy →
pytest in sequence. mypy exited 1 on fifteen errors, so pytest never executed on
any push. The suite's green-ness was unverified everywhere.

**Eleven tests were muted with `xfail(strict=False)`.** All eleven were
investigated. **Every one is a defect in the test, not in the application.** Six
assert on a Qt state machine synchronously without letting the settle timer
fire. Four assume the three calibration pass types produce equal-length
sequences, which they do not and were never meant to. One feeds a 999 V sentinel
into a current-monitor channel, producing a 9990 mA reading, and the hard trip
correctly aborts — the interlock did its job and the test called it a failure.

**The muting was instructed, not accidental.** `.claude/pending-plan.md` §7.1
told an implementing model to add `@pytest.mark.xfail(reason=..., strict=False)`
above every failing test. Having no way to distinguish a broken test from broken
code, and no permitted move other than suppression, it wrote plausible reasons
for all of them. Three of those reasons describe a "0.1 kV inner ladder" that
does not exist anywhere in the codebase. A number needed explaining, so an
explanation was invented.

**A written rule with no enforcement was ignored 951 times.** `CLAUDE.md` §8
already said GUI tests should assert on what the snapshot pipeline produced, not
on internal widget state. The Qt-dependent files contain 951 private-attribute
accesses, and 5 of 80 test files use the sanctioned `tests/payloads.py` path.

**Nothing tested cross-tab agreement.** Several tabs render the same device.
Each had tests poking its own widget; none compared two views of one device. Two
tabs could disagree about an amplifier voltage and the suite would stay green.

This application controls beamline hardware in a laboratory where an operator
reads a number off a screen and acts on it. A test suite that can be made green
by editing the test is worse than no suite, because it is trusted.

## Decision

**1. Tests are written before the code.** Write the test, observe it fail, then
write the code. The observed failure is what proves the test can detect the
absence of the behaviour. The implementing agent runs pytest in its own session
for each cycle; the Qt-free portion of the suite runs in about ten seconds.

**2. No failure may be muted.** `xfail_strict = true` is set, so a muted test
that starts passing becomes a failure. `xfail` is not a tool for making a build
green. Neither is deleting an assertion, loosening a tolerance, or narrowing a
test's inputs until it stops failing.

**3. When you cannot fix it, stop and report.** This is the only alternative to
fixing a failure. Commit the finished work to the branch. Set the ticket's own
`Status:` line to `blocked` and append what was tried, what failed and what needs
deciding under its `## Comments` heading, per the issue-tracker convention in
`docs/agents/issue-tracker.md`. Open the PR as a **draft**. Master is not touched.
The ticket file travels with the branch, so the draft PR and its failing CI run
are the report. Do not suppress, do not work around, do not guess at a domain
decision.

The report must live in a tracked directory. `.claude/` is gitignored in this
repository, so nothing written there is ever pushed and no reviewer would see it.

**4. Every error is reported; gating is layered and written down.** mypy checks
all of `src/` with no named-file exclusions. Errors in `config/` and `hardware/`
fail the build. Errors elsewhere are printed and counted, and the count may only
decrease. Ruff's blanket ignores are removed on the same terms. A check that is
skipped in secret is a muted failure by another name; a bar that differs by
layer and says so in one visible config block is not.

**5. State a rule once, across the app.** Prefer a parametrized contract test
that asserts an invariant against every tab to the same assertion repeated per
widget. A contract test covers the next tab automatically; a per-widget test
requires someone to remember. `tests/test_layering.py` is the existing model for
this.

**6. A GUI test earns its place by driving the production path.** It pushes data
through `tests/payloads.py` or a real `Beamline` and asserts on something an
operator could see or a downstream consumer reads. A test that calls a private
method and reads a private attribute is deleted, or rewritten through the
pipeline if it covers real behaviour.

### Vocabulary this establishes

- **Contract test** — one test stating one rule, run against every subject the
  rule binds. Adding a subject extends the coverage automatically.
- **Churn test** — a test that fails when code is restructured without any
  behaviour changing, and that would not have caught a real defect. Its cost is
  paid on every refactor and its benefit is zero.
- **Cross-tab agreement** — see `CONTEXT.md`. Now a contract test, not an
  assumption.

## Consequences

**Accepted costs.** Test-first is slower per feature. Deleting churn tests will
drop the headline test count substantially — from 817 Qt-dependent tests to an
estimated 350–450 — and a smaller number will look like a regression to anyone
reading counts instead of coverage. It is not one. `xfail_strict` means an
environment problem breaks the build loudly instead of degrading quietly, which
is the intent.

**Explicitly not adopted.** Mutation testing was considered and declined as too
slow to be worth its setup here. The consequence is accepted and recorded: the
suite can show that `hardware/` math runs, but nothing proves those tests would
notice a subtly wrong formula. If a published number is ever traced to a bad
constant, this is the decision to revisit first.

**What would reverse this.** If test-first proves unworkable for exploratory
hardware bring-up — where the correct behaviour is genuinely unknown until an
instrument is on the bench — the honest response is to carve out a named,
documented exception for that work. It is not to quietly resume writing tests
after the fact.
