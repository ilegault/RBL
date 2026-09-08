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

**Status:** done

- [x] Every test function in the batch carries exactly one verdict.
- [x] Every verdict carries a reason naming which criterion it met.
- [x] Verdicts are written as a reviewable file under `.scratch/test-suite-overhaul/`.
- [x] The batch's file list is recorded in that file.
- [x] Every `rewrite` verdict names the sanctioned path the rewrite would use.
- [x] No test file is modified.

Reference: spec section "The audit"; ADR 0001 vocabulary and decision 5.

## Comments

Done 2026-09-08: audited all 20 files in the batch (259 test functions, counted via an AST
`def test_` walk per file rather than a naive grep, per the false-positive ticket 07 hit). The
batch's file list is exactly the complement of ticket 07's recorded batch — cross-checked
directly against `.scratch/test-suite-overhaul/audit-07-hardware-and-pure-math.md`'s own "Batch
file list" section, so the two batches partition the 48 non-Qt test modules with no overlap and
no gap (28 + 20 = 48). Verdicts, reasons, and the file list are written to
`.scratch/test-suite-overhaul/audit-08-state-services-persistence.md`.

Result: 242 KEEP, 4 REWRITE, 13 DELETE. `tests/test_layering.py` is verdicted `keep` as the
explicit template, per the ticket's instruction, with a reason naming why (one rule, checked
across every module, with a tracked exception list matching documented architecture debt).

The 4 rewrites are all in `tests/test_beamline.py`'s `TestHvInterlock` class: they write the
interlock's private pressure cache (`_hv_pressure_torr`, `_hv_pressure_at`) or call the private
`_recompute_hv_interlock()` directly, when a public production entry point
(`on_vacuum_changed_for_interlock`, wired to `vacuum_changed` in production) exists and is
already exercised unprimed by two sibling tests in the same class. Each names that sanctioned
path.

The 13 deletes split two ways. Ten are in `tests/test_tab_persistence.py`: the file's own
`StubGalilPollThread`/`StubLabJackPollThread` classes are the test author's own from-scratch
reimplementations, not the real production `GalilPollWorker`/`LabJackPollWorker` — and
`LabJackPollWorker` does not exist anywhere in `src/` at all (confirmed by a full-repo grep), so
half of this file tests a class of its own invention. These tests cannot catch a defect in
production code because they never call any; the file's own two remaining tests, which do drive
the real `RollingBuffer` class, are kept. The other 3 deletes: 2 in `test_labjack_stream.py`
write a constructor argument into a private attribute and read the identical attribute straight
back without ever exercising `_resolve_scan_names()` (its only real consumer, confirmed
untested anywhere else in the suite by grep); 1 in `test_beamline.py` defines two "distinct call
sites" that are the literal same expression, so it cannot fail for the reason its docstring
claims and duplicates a sibling test that already covers the same rejection.

A handful of `keep` verdicts on the private-access line are called out explicitly in the audit
file's "Flagged for the reviewer" section rather than reached-for-rewrite, because
`labjack_stream_worker.py`'s own docstring and its own `__main__` self-test block establish the
`__new__` + `_build_payload(...)` technique as the module author's sanctioned unit-test seam for
this pure array math — not an accidental internals leak. Also flagged there: the shared
`beamline` fixture in `test_beamline.py` (used by ~40 of the file's 53 tests) primes the
interlock via the same private attributes as a systemic convenience; noted once rather than
forcing 40 individual rewrite verdicts, since a single future fixture rewrite fixes it for every
consumer at once, and one harmless tautological assertion inside an otherwise-real
`test_snapshot_json.py` test.

No test file, ADR, or production module was modified while producing this audit.
