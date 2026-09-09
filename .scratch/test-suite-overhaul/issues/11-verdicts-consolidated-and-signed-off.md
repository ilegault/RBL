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

**Status:** done

- [x] Every test module in the repository appears in exactly one batch (80 files checked, 0 gaps).
- [x] Every test function carries exactly one verdict (1,620 test functions audited).
- [x] Totals per verdict (1,256 Keep, 263 Rewrite, 101 Delete) and the projected post-audit test count (1,519 test functions) against the baseline are stated.
- [x] The "would have caught nothing" lists are gathered in one place.
- [x] The consolidated list is presented to the developer and their decision is appended to the file (Approved 2026-09-08).
- [x] No test file and no application source file is modified.

Reference: spec section "The audit" — "no test is deleted before the verdicts are reviewed."

## Comments

Consolidated audit report prepared and signed off at `.scratch/test-suite-overhaul/audit-verdicts-consolidated.md` combining batches 07, 08, 09, and 10:
- **Batch 07 (Hardware & Pure Math):** 28 files, 748 tests (723 Keep, 0 Rewrite, 25 Delete)
- **Batch 08 (State, Services & Config):** 20 files, 259 tests (242 Keep, 4 Rewrite, 13 Delete)
- **Batch 09 (Qt Modules Batch 1):** 17 files, 296 tests (150 Keep, 95 Rewrite, 51 Delete)
- **Batch 10 (Qt Modules Batch 2):** 15 files, 317 tests (141 Keep, 164 Rewrite, 12 Delete)

**Totals across all 80 test files (1,620 tests):**
- **1,256 Keep** (77.5%)
- **263 Rewrite** (16.2%)
- **101 Delete** (6.2%)
- **Projected post-audit test count:** 1,519 test functions (before tickets 15 & 16 contract additions).

Signed off by developer on 2026-09-08. Tickets 12, 13, and 14 are now unblocked.


