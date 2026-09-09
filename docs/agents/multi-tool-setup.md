# Running the same procedure from Claude Code and Google Antigravity

## The problem

The working procedure for this repo lives in three places:

1. **`CLAUDE.md`** — the conventions. Layering rules, threading contract, testing
   rules, the escalation protocol, and the `ACTIVE-PLAN` block naming the current
   ticket set.
2. **Claude Code skills** — `/grill-with-docs`, `/to-spec`, `/to-tickets`,
   `/implement`, `/code-review`. Procedure encoded as commands.
3. **The ticket files** in `.scratch/<effort>/issues/`.

Antigravity reads none of the first two. It does not read `CLAUDE.md` — that
filename is specific to Claude Code — and it cannot run a Claude Code skill. Point
Antigravity at this repo today and it sees the source, the tickets if you name them,
and nothing else. Every convention in `CLAUDE.md` is invisible to it, including the
rule that a failing test is fixed or escalated and never muted.

That is the gap. Closing it means moving the parts of the procedure that are
*enforcement* into a file both tools read, and accepting that the parts that are
*commands* stay where they are.

## What each tool actually reads

| | Claude Code | Antigravity |
|---|---|---|
| `CLAUDE.md` | yes | **no** |
| `AGENTS.md` | **no** (but see below) | yes |
| `GEMINI.md` | no | yes, and it **overrides** `AGENTS.md` |
| `.agent/rules/` | no | yes, as a supplement |
| Nested `AGENTS.md` in subfolders | n/a | yes, if enabled in Settings → Agent |

Claude Code does not read `AGENTS.md` directly, but it supports an **import**
syntax: a line reading `@AGENTS.md` in `CLAUDE.md` pulls that file's contents in.
That import is the hinge this whole setup turns on.

> Sourcing note: the Claude Code side is documented behaviour. The Antigravity
> specifics above come from community documentation rather than a first-party
> reference, so verify the nested-file setting and the `GEMINI.md` precedence on
> your install before relying on either.

## The setup

**One authored file, read by both tools. No copies, no generation step.**

`AGENTS.md` becomes the canonical conventions file, holding everything `CLAUDE.md`
holds today *including* the `ACTIVE-PLAN` block. `CLAUDE.md` shrinks to a single
import line.

### Steps — done on 2026-09-09

1. **`CLAUDE.md` renamed to `AGENTS.md`.** Done with a plain `mv`; git detects the
   rename by content similarity at commit time. Not staged — commit it yourself.
2. **New `CLAUDE.md` created**, containing exactly one line: `@AGENTS.md`.
   Nothing else belongs in it. Any second line is a line Antigravity cannot see.
3. **`set_plan.py` retargeted** to write the `ACTIVE-PLAN` block into `AGENTS.md`.
   Its `--git` staging now covers `AGENTS.md`, `CLAUDE.md` and `.claude/plans/`,
   so the stub gets committed on the first remote-mode run. Its docstring records
   why, so nobody points it back at `CLAUDE.md` later. Verified: a run writes to
   `AGENTS.md` and leaves the markers and surrounding content intact.
4. **Implementation protocol added** to `AGENTS.md` §11, immediately above the
   `ACTIVE-PLAN` block. Nine numbered steps: read the ticket and its ADRs, work the
   frontier, the two-word status vocabulary, one ticket per branch, run the suite,
   never mute a failing test, the four-step escalation, why the report goes in
   `.scratch/` and not `.claude/`, and update the docstring's reasoning.

### Still to do — yours

- **Update the Cowork project instructions.** They tell the planner to read
  `<REPO>/CLAUDE.md` first and describe the `ACTIVE-PLAN` block as living there.
  Both references should now say `AGENTS.md`. This file lives in the Cowork UI, not
  the repo, so it cannot be changed from a session.
- **Commit the rename.** Nothing here was staged.

### Why not a symlink

Making `CLAUDE.md` a symlink to `AGENTS.md` also works and is one fewer moving
part. It is not recommended *here*: this repo is developed on Windows, git symlinks
on Windows need `core.symlinks` and Developer Mode, and this tree already has a
history of line-ending and checkout friction. The import line has none of that
exposure.

### Why not two files kept in sync

Because they will not stay in sync. The repo already states the principle in
another context — two sources of truth for the same thing is worse than one in the
wrong place. A generated `AGENTS.md` is the same trap with an extra step to forget.

## What cannot move, and what to do about it

The Claude Code skills are commands, not documents. Antigravity cannot run
`/grill-with-docs`, `/to-spec`, `/to-tickets` or `/code-review`. This is fine,
because those are all **planning** commands and planning already happens in
Cowork/Claude Code. The division that results:

- **Planning — Claude Code / Cowork only.** Grill the design, write the spec, break
  it into tickets, set the `ACTIVE-PLAN` pointer. Unchanged.
- **Implementation — either tool.** Both read `AGENTS.md`; both can read a ticket
  file; both can run `pytest`.
- **Review — Claude Code only**, unless the checklist moves into `AGENTS.md`.

The consequence: anything an implementing agent must obey has to be in `AGENTS.md`,
not in a skill. `/implement` currently supplies some of that framing for Claude Code
sessions. Antigravity gets nothing equivalent, so `AGENTS.md` needs an explicit
implementation-protocol section stating, at minimum:

- Read the ticket named in `ACTIVE-PLAN`, and read the ADRs it references.
- Work the frontier — never start a ticket whose `Blocked by:` names an unfinished one.
- One ticket per branch; open a pull request per ticket.
- Run `pytest` before and after. A failing test is fixed or escalated, never muted —
  no expected-failure markers, no weakened assertions, no loosened tolerances, no
  narrowed inputs.
- The escalation path: commit finished work to the branch, set the ticket's own
  `Status:` line to `blocked`, append what happened under the ticket's `## Comments`
  heading, open the pull request as a draft.
- The escalation report goes in the ticket file under `.scratch/`, never under
  `.claude/` — that directory is gitignored, so anything written there is never
  pushed and no reviewer sees it.

Most of this already exists in the conventions file. What is missing is that it is
currently addressed to "AI sessions" in prose, and an implementing agent arriving
cold at ticket 05 needs it as a checklist.

## Traps

**Do not create a `GEMINI.md`.** It takes priority over `AGENTS.md` in Antigravity
and is invisible to Claude Code. A `GEMINI.md` with real content in it recreates
exactly the split this setup removes, and it will win silently.

**Do not put anything in the new `CLAUDE.md` except the import line.** The moment a
second line appears, Claude Code and Antigravity are reading different instructions
again.

**Nested `AGENTS.md` files are off by default.** If you ever add one under `src/`,
turn on Settings → Agent → Load nested AGENTS.md files, and remember Claude Code
will not see it at all unless it is also imported.

**Ticket status vocabulary drifted across tools.** The test-suite-overhaul set was
closed out with three different spellings — `done`, `complete`, and `completed`.
Pick one, state it in `AGENTS.md`, and the frontier stays machine-readable no matter
which tool wrote the last status line.

## Verifying it worked

1. Start a Claude Code session and ask what the active plan is. It should name the
   Faraday cup ticket set and ticket 01. If it does not, the import is not resolving.
2. Start an Antigravity session and ask the same question. Same answer expected.
3. Ask both: "what happens when a test fails and you cannot fix it?" Both should
   describe the four-step escalation. If Antigravity does not, the implementation
   protocol section is missing or too buried.
4. Run `set_plan.py` once and confirm it wrote into `AGENTS.md` and archived to
   `.claude/plans/`.
