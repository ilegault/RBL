# 168: The cup log records the raw contact word

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 167

**Spec:** `.scratch/cup-status-fix/spec.md` (D4)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `docs/adr/0003-commanded-and-confirmed-cup-position.md`
**Glossary:** `CONTEXT.md` — **Raw contact word**, **Confirmed position**
**Read first:** `ingest_cup_actuation_window` and `_publish_cup_actuation_snapshot` in `src/rbl/state/cup_actuation_link.py`; `CupActuationState` in `src/rbl/snapshots.py`; `write_position_transition` and `write_fault_impossible_status` in `src/rbl/services/cup_session_writer.py` (the latter already formats `raw_status`); the two writer calls in `FaradayCupTab` (search `write_position_transition(` and `write_fault_impossible_status(`) in `src/rbl/gui/faraday_cup_tab.py`; `test_all_four_fault_kinds_distinguishable_by_record_type` in `tests/test_cup_session_writer.py`; `TestSamplingCycleTab.make_tab_and_feed` and the CSV reading in `test_skip_recorded_when_manual_run_open` in `tests/test_faraday_cup_tab.py`.

## What to build

The cup log has a `status_word` column, but it is empty on every position and fault row,
because the snapshot the tab receives never carries the word the contacts produced. Had it
been filled on 2026-11-10, the polarity error fixed in ticket 167 would have been visible in
the file. After this ticket:

- `CupActuationState` gains `confirmed_raw: int | None = None`, documented in its Fields
  list: the raw `FIO_STATE` word of the scan segment whose decode became the current
  confirmed position; `None` until the first confirmation.
- `ingest_cup_actuation_window` stores that segment's word (`val`) at the moment it sets a
  new `_cup_confirmed`, and `_publish_cup_actuation_snapshot` passes it on.
- `write_position_transition` gains `raw_status: int | None = None` and writes it to
  `status_word` exactly as `write_fault_impossible_status` does (`0x%08X`, empty when None).
- `FaradayCupTab` passes `raw_status=state.confirmed_raw` to both writer calls.

## Acceptance criteria

- [ ] Test `test_confirmed_position_carries_its_raw_word`, in `tests/test_cup_actuation.py`:
      with `CupActuationFeed` and snapshots collected from `beamline.cup_actuation_changed`,
      send word 8 at t=1.0, then word 4 at t=1.2; the last snapshot after the first has
      `confirmed == CupPosition.OUT` and `confirmed_raw == 8`, the last after the second has
      `confirmed == CupPosition.IN` and `confirmed_raw == 4`.
- [ ] Test `test_position_transition_row_writes_raw_status_word`, in
      `tests/test_cup_session_writer.py`: `write_position_transition(t_host=1.0,
      position=CupPosition.IN, raw_status=0x04)` writes a row whose `status_word` is
      `0x00000004`; a call without `raw_status` writes an empty `status_word`.
- [ ] Test `test_cup_log_rows_carry_the_raw_contact_word`, in
      `tests/test_faraday_cup_tab.py`: with a cup log open on a temp path (as
      `make_tab_and_feed` opens one) and a `CupActuationFeed` on the same `Beamline`, send
      8, then 4, then 0, one window each; the CSV's `position_transition` rows have
      `status_word` `0x00000008` then `0x00000004`, and its `fault_impossible_status` row has
      `0x00000000`. Place this test in the same class, directly after
      test_actuation_indeterminate_status_presents_as_fault.
- [ ] Existing snapshot, writer and tab tests still pass unchanged; `confirmed_raw` has a
      default so every existing `CupActuationState(...)` construction still works. (no test: CI's full suite)

**What tests may fake:** the LabJack (`ljm`) and the stream worker. The writer writes a real
file under `tmp_path`.

**Out of scope:** raw words on any other record type; logging in-transit words that never
confirm; the interlock (tickets 171–172).

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_cup_actuation.py tests/test_cup_session_writer.py tests/test_faraday_cup_tab.py
```

Run pytest on the touched test files only (implement-ticket-parallel, section 4). CI runs the full suite on the PR.

## Comments
