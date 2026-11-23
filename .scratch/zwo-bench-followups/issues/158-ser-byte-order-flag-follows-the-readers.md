# 158: SER files say "little-endian" the way SER readers expect

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/zwo-bench-followups/spec.md` (D1; user stories 1-3)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 6: the Master Recording is never converted), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/hardware/ser_format.py` (module docstring, `SER_LITTLE_ENDIAN_FLAG`, `pack_header`), `src/rbl/capture/recording.py` (where frames become `<u2` bytes), `tests/test_ser_format.py` (`_decode_ser_header`, the header round-trip test and the frames-and-trailer round-trip test, for how a SER file is written and read back), bench ticket `.scratch/zwo-capture/issues/79-bench-end-to-end-with-the-real-camera.md` (its 2026-11-20 comment).

## What to build

On the bench, every 16-bit Segment opened in SER Player as coloured noise. The pixel bytes are right (little-endian), but the header's `LittleEndian` field is 1. The SER v3 document says 1 means little-endian, yet SER Player, Siril, FireCapture, Lynkeos and almost every other reader follow the first writers and read **0** as little-endian, so they byte-swap RBL's files.

1. `src/rbl/hardware/ser_format.py`: set `SER_LITTLE_ENDIAN_FLAG: int = 0`. In the module docstring, change the header bullet to `LittleEndian (int32): 0, meaning little-endian pixel data (see BYTE ORDER FLAG)` and add a section `BYTE ORDER FLAG` that says: the SER v3 document defines 1 as little-endian, but SER Player, Siril, FireCapture and Lynkeos read 0 as little-endian, for compatibility with the first SER writers; with 1, RBL's 16-bit segments opened byte-swapped as coloured noise on the 2026-11-20 bench run (ticket 79). RBL writes little-endian (`<u2`) pixels and 0. Do not change it back to match the document.
2. Change nothing else: pixel bytes, header layout, the trailer and 8-bit files are unchanged. Files already recorded are not migrated.

## Acceptance criteria

- [ ] (no test: an in-place rewrite of an existing test, which CI runs) Rewrite the existing header round-trip test in `tests/test_ser_format.py` (test_header_fields_round_trip) in place, under the same name, so that its literal assertion on the little-endian field expects 0, not 1. Add a one-line comment pointing at the module's BYTE ORDER FLAG section.
- [ ] Test `test_sixteen_bit_segment_reads_back_little_endian_with_flag_zero` in `tests/test_ser_format.py`: write a 16-bit SER file with this module's writer functions (as the frames-and-trailer round-trip test does), with two 4 × 2 frames whose values include 0x0102 and 0xABCD. The little-endian field (int32 at byte offset 18) reads 0; the payload decoded as `<u2` equals the frames exactly; the same bytes decoded as `>u2` do **not** equal them (so the test proves the byte order, not just the flag).
- [ ] (no test: CI's full suite) No test is deleted, and every existing SER, recording and Capture Process test passes.

**What tests may fake:** nothing. Real bytes are written to `tmp_path`.

**Out of scope:** repairing existing SER files; the SER spec document link; 8-bit handling; any reader code.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_ser_format.py
```

Run pytest on the touched test files only (implement-ticket-parallel, section 4). CI runs the full suite on the PR.

## Comments

