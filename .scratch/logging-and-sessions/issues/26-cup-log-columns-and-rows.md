# 26: Cup log columns for origin and totals, a continuation header, and a restart row

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** cup-settings/16 (`.scratch/cup-settings/issues/16-keep-previous-schedule.md`; cup-settings tickets 05-08 edit this same file)

**Spec:** `.scratch/logging-and-sessions/spec.md` (sections C4, C5, C6)
**Binding:** `docs/adr/0003-commanded-and-confirmed-cup-position.md` (2026-10-05 amendment), `docs/adr/0002-cup-acquisition-triggered-by-current.md` (decision 3, amendment A8), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/services/cup_session_writer.py`: `CSV_COLUMNS`,
`__init__`, `_write_header_comments` (the `# record types:` line),
`write_insertion_summary`, and the cycle armed/disarmed writers cup-settings ticket 16
added. `tests/test_cup_session_writer.py`.

## What to build

The file format the rest of Part C writes into. Writer only; no caller changes yet.

- `CSV_COLUMNS` gains `origin`, `counted_in_dose`, `total_beam_on_s`, appended after
  every existing column (existing column order unchanged, so old analysis keeps working).
- `write_insertion_summary` gains keywords `origin: str = "automatic"`,
  `counted_in_dose: bool = True`, `total_beam_on_s: float | None = None`.
  `counted_in_dose` is written `true` / `false`. `origin` is one of `automatic`,
  `manual`, `forced`, `uncommanded`; anything else raises `ValueError`.
- `__init__` gains `continuation: dict | None = None`. When given, the `#` header gains a
  line `# dose continued from: <source_path>` and a line listing `total_charge_c`,
  `total_beam_on_s`, `insertion_count` and `beam_on_during_gap` with their values.
- New `write_automatic_insertion_restarted(t_host, gap_start_t, gap_end_t,
  beam_on_during_gap: bool, mode: str)` writing `record_type`
  `automatic_insertion_restarted`, added to the `# record types:` header line.

Tests may fake: nothing. Write to `tmp_path` and read back with `csv` (skip `#` lines).

## Acceptance criteria

- [x] A summary row written with `origin="manual", counted_in_dose=False,
      total_beam_on_s=12.5` reads back with those three values in those columns; one
      written with defaults reads `automatic`, `true`.
- [x] `origin="banana"` raises `ValueError` and writes no row.
- [x] A writer built with `continuation={...}` has both header lines, with the source
      path and every value; one built without has neither.
- [x] `write_automatic_insertion_restarted(...)` produces one row of that record type
      carrying the gap times, `beam_on_during_gap` and `mode`, and the header's
      `# record types:` line names it.
- [x] Every existing test in `tests/test_cup_session_writer.py` passes unchanged.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
2026-10-08: Done. `cup_session_writer.py` gains the `origin`, `counted_in_dose` and
`total_beam_on_s` columns (appended last), the `continuation` header lines, and
`write_automatic_insertion_restarted`. Tests in `tests/test_cup_session_writer.py`
(`TestSummaryOriginAndTotals`, `TestContinuationHeader`, `TestAutomaticInsertionRestarted`)
cover criteria 1-4; criterion 5 is the 44 pre-existing tests, unchanged. No bench check needed.
