# 07: Audit — hardware and pure-mathematics test modules

**What to build:** A keep / rewrite / delete verdict, with a stated reason, for
every test function in the batch below. Verdicts are written to the tracker as
reviewable output. **No test is deleted, rewritten or otherwise modified in this
ticket.**

**Batch:** the test modules that import only from the hardware and configuration
layers and do not import Qt — roughly twenty-eight files covering the raster and
slit-raster models, beam reconstruction, profile width extraction, BPM
calibration, load and regulation models, edge and AC metrics, amplifier monitor
and drive mathematics, interlock thresholds, instrument protocol parsers, and
the timebase and waveform-ring helpers.

The mathematical assertions in this batch are presumed keepers: they pin
formulas against the instrument manual's worked example and against the
laboratory's own deflection sheet, and they invert a planted beam to recover it.
Confirm them; do not re-litigate them.

The batch is **not** exempt from the bloat criteria. A hardware module can still
restate a guarantee the framework already makes, repeat one assertion across a
dozen near-identical cases that would all fail or all pass together, or read
back state it wrote itself. Verdict those `delete` and name the specific
duplication. Restructuring these modules is out of scope, so this batch issues
`keep` and `delete` verdicts only — do **not** issue `rewrite` here.

**Criteria**, in the vocabulary of the decision record:

- **Delete** — a *churn test*: it fails when code is restructured without
  behaviour changing, and would not have caught a real defect. Also delete if it
  restates a guarantee the framework already makes, or if it writes the private
  state it then reads back.
- **Rewrite** — covers real behaviour but reaches it by calling a private method
  or reading a private attribute. Not used in this batch.
- **Keep** — drives the production path and asserts on something an operator
  could see or a downstream consumer reads, or tests hardware-layer mathematics
  directly.

**Blocked by:** 06

**Status:** done

- [x] Every test function in the batch carries exactly one verdict.
- [x] Every verdict carries a reason naming which criterion it met.
- [x] Verdicts are written as a reviewable file under `.scratch/test-suite-overhaul/`.
- [x] The batch's file list is recorded in that file, so ticket 11 can confirm the four batches cover every test module with no overlap.
- [x] Tests pinning a formula to the manual's worked example or the laboratory deflection sheet are verdicted `keep`.
- [x] Bloat inside the batch is verdicted `delete` with the specific duplication or framework guarantee named.
- [x] No `rewrite` verdict is issued in this batch.
- [x] No test file is modified.

Reference: spec section "The audit"; ADR 0001 vocabulary. The bloat scope for this
batch is a developer decision of 2026-09-08 widening the spec's "confirms rather
than re-litigates"; the spec's out-of-scope on *rewriting* these modules still holds.

## Comments

Done 2026-09-08: audited all 28 files in the batch (749 `def test_` lines matched by
a naive grep; one was a false positive from a duplicate class-scoped name collision
across two test files, so the true count is 748 test functions). Verdicts, reasons,
and the exhaustive batch file list are written to
`.scratch/test-suite-overhaul/audit-07-hardware-and-pure-math.md`.

Result: 723 KEEP, 25 DELETE, 0 REWRITE. All manual-worked-example, lab-deflection-sheet,
and planted-beam/fiducial-inversion tests were confirmed as presumed keepers, not
re-litigated. The 25 deletes are all bloat: most are near-identical duplicates of
another test in the same file that would pass or fail together with it (e.g. a
sentinel-value check repeated per channel when the parser under test never branches
on channel); a few restate a guarantee already made by Python/numpy or by the
module's own hardcoded config literal. Each delete verdict names the specific
duplicate or guarantee. No test file, ADR, or production module was modified while
producing this audit.

Two `keep` verdicts in `tests/test_waveform_period.py` were flagged (not acted on,
out of this batch's keep/delete-only scope) as private-attribute/private-method
access that would ordinarily be a `rewrite` candidate — named explicitly in the
audit file's "Flagged for ticket 11" section for the review gate.

The batch's file list was derived by cross-checking against ticket 08's stated
scope (~20 state/services/persistence files) so the two batches partition the 48
non-Qt test modules with no overlap and no gap; this is recorded in the audit
file's "Batch file list" section for ticket 11 to confirm independently.
