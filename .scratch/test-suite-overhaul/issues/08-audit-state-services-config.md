# 08: Audit — state, services and persistence test modules

**What to build:** A keep / rewrite / delete verdict, with a stated reason, for
every test function in the batch below. Verdicts are written to the tracker as
reviewable output. **No test is deleted, rewritten or otherwise modified in this
ticket.**

**Batch:** the test modules that do not import Qt and are not in ticket 07's
batch — roughly twenty files covering the beamline object, snapshot
serialisation, the LabJack link and stream ingestion, calibration configuration
and writing, the history and log writers, persistence stores, function-generator
safety, the interlock link, stream payload statistics, tab persistence, and the
layering check.

The layering test is the model this project wants more of: one rule, checked
across every module. Verdict it `keep` and say why, so the reviewer sees the
template named.

**Criteria**, in the vocabulary of the decision record:

- **Delete** — a *churn test*: it fails when code is restructured without
  behaviour changing, and would not have caught a real defect. Also delete if it
  restates a guarantee the framework already makes, or if it writes the private
  state it then reads back.
- **Rewrite** — covers real behaviour but reaches it by calling a private method
  or reading a private attribute. A rewrite drives the subject through the
  sanctioned data path instead.
- **Keep** — drives the production path and asserts on something an operator
  could see or a downstream consumer reads, or tests hardware-layer mathematics
  directly.

**Blocked by:** 06

**Status:** ready-for-agent

- [ ] Every test function in the batch carries exactly one verdict.
- [ ] Every verdict carries a reason naming which criterion it met.
- [ ] Verdicts are written as a reviewable file under `.scratch/test-suite-overhaul/`.
- [ ] The batch's file list is recorded in that file.
- [ ] Every `rewrite` verdict names the sanctioned path the rewrite would use.
- [ ] No test file is modified.

Reference: spec section "The audit"; ADR 0001 vocabulary and decision 5.
