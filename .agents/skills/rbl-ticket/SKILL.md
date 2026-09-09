---
name: rbl-ticket
description: Implement one ticket from an RBL ticket set end to end — orientation, optional parallel agents, implementation, the full local gate, adversarial self-review, and the PR. Use whenever asked to implement, work, pick up or continue a ticket, work the frontier, or act on the ACTIVE-PLAN block in AGENTS.md.
---

# Implementing one RBL ticket

This repo controls beamline hardware in a laboratory. An operator reads a number
off a screen and turns a knob based on it. That is why the conventions here are
strict and why a green build that was made green by editing a test is worse than a
red one.

Work **one ticket**. Not two, not a ticket and a half. If you finish early, stop and
report; do not wander into the next ticket.

## 1. Orient before touching anything

Read, in this order:

1. **`AGENTS.md`** — the whole file. Layering, threading contract, conventions,
   known traps, and the `ACTIVE-PLAN` block at the bottom naming the current ticket
   set and which tickets are unblocked.
2. **The ticket file** under `.scratch/<effort>/issues/NN-*.md`. Its `Blocked by:`
   line and its acceptance criteria are the contract.
3. **Every ADR the ticket references**, in `docs/adr/`. They are binding, not
   background. A ticket that names one expects you to have read it.
4. **`CONTEXT.md`** — the domain glossary. Use its words. If a term you need is
   missing or the code contradicts it, say so; do not silently pick a side.
5. **The module docstring of every file you are about to edit.** This codebase puts
   its reasoning in docstrings rather than a wiki. The docstring usually already
   answers "why is it like this", and editing against it is how regressions happen.

**Work the frontier.** Never start a ticket whose `Blocked by:` names an unfinished
one. If the only unblocked ticket is marked a developer bench task, say so and stop —
do not claim it, do not simulate it, do not build around it.

## 2. Decide whether to split into parallel agents

Splitting is powerful and frequently wrong here. Judge by the seam, not by size.

**Split when the pieces do not share a seam:**

- **Independent tickets.** Two unblocked tickets touching unrelated files (say, a
  build-tooling cleanup and a UI reorder) run cleanly as two agents on two branches.
  This is the split that actually pays.
- **Implementation and tests, written independently.** One agent implements from the
  ticket. A second agent writes the tests from the ticket's acceptance criteria and
  the spec **without reading the implementation**. Then reconcile. This is the single
  most valuable split in this repo, because it is the only cheap defence against
  tests shaped to pass rather than tests that check behaviour. A test written by
  someone who has just read the code tends to assert what the code does.
- **A survey across many files** where each file's verdict is independent — an audit,
  a convention sweep, a search for every call site of something.

**Do not split when:**

- **The ticket is one vertical slice.** These tickets are deliberately cut as tracer
  bullets through hardware → state → gui → tests. Splitting one by layer produces
  three agents editing toward an interface none of them owns, then a merge that
  nobody designed. Slower than doing it once, and the result is half-integrated.
- **The pieces share a test seam.** If two agents would both edit the shared test
  payload helpers, they will collide.
- **You are splitting to go faster on something small.** Coordination overhead
  exceeds the work. One agent, one pass.

State your split decision and your reason in one line before you act on it.

## 3. Implement

Follow `AGENTS.md` §2 and §7. The rules that get broken most often here:

- **Imports flow downward only:** `gui → services → state → hardware → config`.
  Four upward imports exist and are documented as defects. Do not add a fifth.
- **One instrument, one owner.** No widget constructs, holds, or tears down a driver.
  `Beamline` owns instruments; tabs reach them through methods or signals.
- **One conversion.** Raw volts become physical units in exactly one place, published
  as frozen dataclass snapshots. A widget that converts is a bug: two screens can
  then disagree about one physical reading.
- **Every number has one home.** New tunables go in `rbl/config/`, never as literals
  in a widget or a service.
- **Workers communicate only by Qt signals.** Never call a widget method from a
  worker thread.
- **Devices are optional.** Every tab must work with nothing connected, and must show
  "not connected" rather than a stale or zeroed value.
- **Numeric entry uses the project's own input widgets**, never a bare spin box.
  Colours come from theme roles, never hex codes.
- **Docstrings explain WHY**, with a `WHY THIS EXISTS` section recording the bug, the
  constraint, or the physical fact that forced the design. When you change behaviour,
  update the reasoning. A stale *why* is worse than none.
- Prefer `logging.getLogger(__name__)` over `print()` in new code.

## 4. Run the full gate locally, before the PR

CI runs four checks and they fail in this order. Run all four yourself. Do not open a
pull request and let CI find these — that round trip is the main way time gets wasted
on this repo.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

Notes that matter:

- **The type gate is layered, not a bare mypy run.** Any error under `rbl.config` or
  `rbl.hardware` fails the build outright. Everything else counts against the ratchet
  in `tools/mypy_ratchet.txt`, which **may only decrease**. Do not raise it. If your
  change would raise it, fix the types instead.
- **`tools/private_access_ratchet.txt` works the same way.** Tests reaching into
  private widget attributes may only go down. Assert on rendered output and on what
  the snapshot pipeline produced, not on internal state.
- **`check_tests_first.py` enforces that tests come first.** It is a real gate, not
  advice.
- Qt runs offscreen automatically via `tests/conftest.py`. You do not need a display.

## 5. Review your own diff, adversarially

Before the PR, review the change as if you were looking for the reason it will be
reverted. If you split agents, this is a good place to use a fresh one: a reviewer
that has not just written the code sees more.

Check, concretely:

- **Every acceptance criterion on the ticket** — tick them off individually, not in a
  batch. If one cannot be ticked, the ticket is not done.
- **Does it break an invariant in `AGENTS.md` §2?** Layering, one owner, one
  conversion, one home for every number.
- **Cross-tab agreement.** If your change touches something rendered on more than one
  tab, can two screens now disagree about one physical value? Nothing in the suite
  historically caught this; you have to look.
- **Do the tests actually fail if the behaviour is wrong?** Break the implementation
  deliberately and confirm the test goes red. A test never observed failing is not
  known to work. This is the point of the exercise.
- **Are the tests asserting behaviour or implementation?** A test that would break on
  a rename but not on a wrong number is testing the wrong thing.
- **Did you leave a stale docstring** describing behaviour you just changed?
- **Line endings.** `.gitattributes` normalises to LF. If `git status` shows far more
  files changed than you touched, stop and investigate rather than committing it.

## 6. Land it, or escalate

### Landing the ticket (when all gates pass)

Follow this sequence once the local gate (ruff, check_tests_first, type_gate, pytest) passes and adversarial review is complete:

1. **Update ticket file**: Set `Status: done` (exact casing) and tick all acceptance criteria checkboxes `[x]` in `.scratch/<effort>/issues/NN-*.md`.
2. **Commit all changes**: Commit application code, tests, docstrings, and the updated ticket file to the ticket branch. Never commit directly to `master`.
3. **Push the branch**: Push the branch to remote with `git push -u origin <branch-name>`.
4. **Create the Pull Request**:
   - If GitHub CLI (`gh`) is installed and authenticated:
     ```powershell
     gh pr create --base master --head <branch-name> --title "<ticket title>" --body "<structured summary with criteria & verification>"
     ```
   - If `gh` is not available:
     Provide the direct GitHub PR creation URL (`https://github.com/<owner>/<repo>/pull/new/<branch-name>`) and output a structured PR block (Title, Summary of changes, Acceptance criteria checklist, and Gate results) ready for submission.

### Failure handling and escalation (never mute a test)

**A failing test is fixed or escalated, never muted.** No `xfail`. No deleted or
weakened assertions, no loosened tolerances, no inputs narrowed until it passes.
Those are all the same move — making the test stop reporting the problem instead of
fixing it — and `docs/adr/0001-tests-first-and-no-muted-failures.md` records what
happened the one time it was tried: eleven false expected-failures, three citing a
calibration ladder step that does not exist anywhere in the codebase. A number needed
explaining, so an explanation was invented. Do not invent.

**When you cannot fix it, escalate in four steps and stop:**

1. Commit the finished, correct work to the branch. Nothing good is thrown away.
2. Set the ticket's `Status:` to `blocked`.
3. Append under the ticket's `## Comments` heading: what you attempted, what failed,
   and what needs a human decision.
4. Push the branch (`git push -u origin <branch-name>`) and open the pull request as a **draft** (`gh pr create --draft ...` or via GitHub web). Master is untouched.

The report goes in the ticket file under `.scratch/`, never under `.claude/` — that
directory is gitignored, so anything written there is never pushed and no reviewer,
human or agent, will ever see it.

Guessing at a domain decision is not an alternative to escalating. This is a physics
instrument; a plausible-looking number is worse than no number.
