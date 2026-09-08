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

**Status:** ready-for-agent

- [ ] A pull request touching application source with no test change fails the check.
- [ ] A pull request touching application source with a test change passes.
- [ ] A test-only or documentation-only pull request passes.
- [ ] The escape is explicit and visible on the pull request, and is documented alongside the blocked-work protocol from ticket 02.

Reference: spec user story 50; ADR 0001 decision 1.
