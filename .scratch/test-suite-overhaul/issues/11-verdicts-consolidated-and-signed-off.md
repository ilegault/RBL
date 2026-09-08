# 11: Audit verdicts consolidated and signed off

**What to build:** One reviewable verdict list covering the whole suite, and a
recorded developer decision on it. This is the gate: **no test is deleted and no
application source is changed until this ticket is signed off.**

The four batch outputs are merged into a single file, ordered so the reviewer
can read it by verdict rather than by file. It states the totals — how many
keep, how many rewrite, how many delete — the resulting projected test count
against the ticket 01 baseline, and the "would have caught nothing" list from
the two Qt batches gathered in one place.

Confirm coverage before presenting it: every test module in the repository
appears in exactly one batch, and every test function in those modules carries
exactly one verdict. A module that fell between batches is a gap, not a
rounding error.

Then stop and present it. The developer disagrees with specific calls or accepts
the list; the decision is appended to the file. An implementing agent does not
sign this off on its own.

**Blocked by:** 07, 08, 09, 10

**Status:** ready-for-agent

- [ ] Every test module in the repository appears in exactly one batch.
- [ ] Every test function carries exactly one verdict.
- [ ] Totals per verdict and the projected post-audit test count against the baseline are stated.
- [ ] The "would have caught nothing" lists are gathered in one place.
- [ ] The consolidated list is presented to the developer and their decision is appended to the file.
- [ ] No test file and no application source file is modified.

Reference: spec section "The audit" — "no test is deleted before the verdicts are reviewed."
