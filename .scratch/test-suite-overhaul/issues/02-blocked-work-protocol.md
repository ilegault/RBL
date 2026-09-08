# 02: The blocked-work protocol is written down and the stale plan is deleted

**What to build:** An implementing agent that cannot fix a failing test has a
documented legal move that is not suppression, and can find it without being
told.

The rule and the procedure land in the repository's standing conventions for AI
sessions, pointing at the decision record rather than restating it: a failing
test is fixed or escalated, never muted, and never worked around by deleting an
assertion, loosening a tolerance, or narrowing a test's inputs until it stops
failing. When an agent cannot fix one it stops. It commits finished work to the
branch, sets the ticket's own status line to `blocked`, appends what was
attempted, what failed and what needs deciding under the ticket's comments
heading, and opens the pull request as a **draft**. The main branch is not
touched. The ticket file travels with the branch, so the draft pull request and
its failing CI run are the report.

The report must land in a tracked directory, and the reason is worth stating
where the agent will read it: the agent working directory is gitignored in this
repository, so anything written there is never pushed and no reviewer sees it.

The stale plan file containing the original instruction to mark every failing
test as expected-to-fail is deleted. It is gitignored, so it exists only on the
developer's disk, where a fresh local session can read it and follow it. Its
contents are already fully implemented in the tree, so deleting it loses nothing.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] The fix-or-escalate rule appears in the standing AI-session conventions and names the decision record as binding.
- [ ] The escalation procedure names all four steps: commit to branch, ticket status `blocked`, comment appended to the ticket, draft pull request.
- [ ] The reason the report must live in a tracked directory is stated, not merely the requirement.
- [ ] `.claude/pending-plan.md` no longer exists.

Reference: spec section "Blocked-work protocol"; ADR 0001 decision 3.
