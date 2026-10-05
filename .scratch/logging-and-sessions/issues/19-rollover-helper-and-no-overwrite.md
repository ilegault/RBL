# 19: The rollover helper, and vacuum files that never overwrite

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/logging-and-sessions/spec.md` (sections A1, A2)
**Binding:** `docs/adr/0004-monitoring-log-and-sessions.md` (decisions 2, 8), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/services/vacuum_logger.py` (module docstring, `_new_run_id`,
`VacuumLogger.__init__`), `src/rbl/config/paths.py` (`VACUUM_DIR`).

## What to build

One pure module that every log uses to decide which month folder a file goes in, what it
is called, whether local midnight has passed, and which name is free. `VacuumLogger`
uses it so a vacuum file can be given a fixed stem (`vacuum` for a session folder, later)
and so no vacuum file is ever overwritten.

New `src/rbl/services/log_rollover.py`, no Qt, no clock (callers pass tz-aware local
`datetime`s from `datetime.now().astimezone()`):
`month_folder(root, local_dt) -> root / "YYYY-MM"`,
`timestamped_stem(prefix, local_dt) -> f"{prefix}_{YYYYMMDDTHHMMSS}"`,
`crossed_local_midnight(opened_local, now_local) -> now_local.date() != opened_local.date()`,
`unused_path(folder, stem, suffix) -> Path` (`stem`, then `stem_2`, `stem_3`, ...; the
only function that touches the filesystem, and only to test existence). Pure so midnight,
month-end and DST are tested by passing times rather than waiting.

`VacuumLogger.__init__` gains keyword `file_stem: str | None = None`. Default stem is
today's run id. The CSV path is `unused_path(output_dir, stem, ".csv")` and the sidecar
uses the same chosen stem with `.json`.

## Acceptance criteria

- [ ] New `tests/test_log_rollover.py`: 23:59:59 to 00:00:00 the next day returns
      `True`; 00:00:00 to 23:59:59 the same day returns `False`; 31 Oct to 1 Nov gives
      folders `YYYY-10` and `YYYY-11`; 31 Dec to 1 Jan gives a new year's folder; two
      times on the November DST fall-back date in America/Chicago (01:30 CDT and
      01:30 CST, built with `zoneinfo`) return `False`.
- [ ] `unused_path` in `tmp_path`: with no file, returns `stem.csv`; after creating it,
      returns `stem_2.csv`; after creating that, `stem_3.csv`.
- [ ] `timestamped_stem("vacuum", datetime(2026,9,29,14,30,12, tzinfo=...))` returns
      `vacuum_20260929T143012`.
- [ ] In `tests/test_vacuum_logger.py` (new test): two `VacuumLogger(..., output_dir=tmp_path,
      file_stem="vacuum")` instances each write a header and a row; the folder holds
      `vacuum.csv` and `vacuum_2.csv`, and the first file's contents are unchanged after
      the second is written. Use the existing `_Fake*` stand-ins in that file.
- [ ] Every existing test in `tests/test_vacuum_logger.py` passes unchanged.
- [ ] The `vacuum_logger.py` module docstring states: logging starts automatically on the
      first reading (the tab does this), files are named `vacuum_YYYYMMDDTHHMMSS`, they
      live under `config.paths.VACUUM_DIR`, and a name collision gets a `_2` suffix rather
      than overwriting. It has a `WHY THIS EXISTS` note recording the two-week, 176 MB file.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
