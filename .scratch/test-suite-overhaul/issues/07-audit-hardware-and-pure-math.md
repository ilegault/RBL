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

**Status:** ready-for-agent

- [ ] Every test function in the batch carries exactly one verdict.
- [ ] Every verdict carries a reason naming which criterion it met.
- [ ] Verdicts are written as a reviewable file under `.scratch/test-suite-overhaul/`.
- [ ] The batch's file list is recorded in that file, so ticket 11 can confirm the four batches cover every test module with no overlap.
- [ ] Tests pinning a formula to the manual's worked example or the laboratory deflection sheet are verdicted `keep`.
- [ ] Bloat inside the batch is verdicted `delete` with the specific duplication or framework guarantee named.
- [ ] No `rewrite` verdict is issued in this batch.
- [ ] No test file is modified.

Reference: spec section "The audit"; ADR 0001 vocabulary. The bloat scope for this
batch is a developer decision of 2026-09-08 widening the spec's "confirms rather
than re-litigates"; the spec's out-of-scope on *rewriting* these modules still holds.
