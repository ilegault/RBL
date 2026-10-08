# 27: The CupLog service: one cup log at a time, and reading totals back

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 19, 25, 26

**Spec:** `.scratch/logging-and-sessions/spec.md` (section C1)
**Binding:** `docs/adr/0002-cup-acquisition-triggered-by-current.md` (2026-10-05 amendment, B1), `docs/adr/0004-monitoring-log-and-sessions.md`, `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/services/cup_session_writer.py` (`__init__`: `session_id` and
`output_dir`, `close`), `src/rbl/services/log_rollover.py`, `src/rbl/hardware/dose_model.py`
(`DoseTotals` from ticket 25), `src/rbl/config/paths.py` (`FARADAY_CUP_DIR`, `LOGS_DIR`).

## What to build

New `src/rbl/services/cup_log.py`, the single owner of whether a cup log is open.

- `class CupLogKind(Enum)`: `SESSION = "session"`, `TEST = "test"`.
- `class CupLog(QObject)` with signals `opened(str path, str kind)` and `closed(str path)`;
  `__init__(self, test_root: Path | None = None)` (defaults to `FARADAY_CUP_DIR`);
  `open_for_session(folder: Path, continuation: dict | None = None) -> CupSessionWriter`
  (`session_id="cup"`, so `cup.csv`); `open_test(now_local: datetime) -> CupSessionWriter`
  (in `month_folder(test_root, now_local)`, stem `timestamped_stem("cup", now_local)`
  through `unused_path`); `close() -> str | None`; properties `writer`, `kind`, `path`.
  Opening while one is open closes it first and emits `closed` before `opened`.
- Pure module functions, no Qt: `read_dose_totals(csv_path: Path) -> DoseTotals | None`
  returns the totals from the last summary row whose `counted_in_dose` is `true`
  (`last_out_t` = that row's confirmed timestamp + dwell; `last_current_a` = its mean
  current), `None` if there is none. `find_previous_session_cup_log(logs_dir: Path,
  exclude: Path) -> Path | None` returns the newest `session_*/cup.csv` by folder name,
  skipping `exclude` and any file where `read_dose_totals` is `None`.
- Frozen dataclasses `RestartChoice(resume_schedule: bool, beam_on_during_gap: bool)`,
  `ContinueChoice(continue_dose: bool, beam_on_during_gap: bool)` and
  `CupView(logging: bool, log_path: str | None, run_open: bool, automatic_running: bool,
  charge_c: float, fluence: float | None, dpa: float | None)`, used by later tickets.

Tests may fake: nothing. Totals tests must read files written by a real
`CupSessionWriter` (with ticket 26's columns) under `tmp_path`, not hand-typed CSV.

## Acceptance criteria

- [x] `open_test` at 2026-09-29 14:30:12 creates `<test_root>/2026-09/cup_20260929T143012.csv`;
      a second `open_test` at the same time gives `..._2.csv` and closes the first
      (`closed` emitted once, with the first path, before `opened`).
- [x] `open_for_session(folder)` creates `folder/cup.csv`; while a test log was open, it
      is closed first. After `close()`, `writer`, `kind` and `path` are `None`.
- [x] `read_dose_totals` on a real writer's file with automatic, automatic, manual rows
      returns the second automatic row's totals; on a file with only manual rows, `None`.
- [x] `find_previous_session_cup_log` over three `session_*` folders (newest has no
      counted rows, middle has, oldest has) returns the middle one, and returns `None`
      when `exclude` is the only folder with counted rows.
- [x] `cup_log.py`'s pure functions import nothing from PySide6 (a test inspects the
      functions' module-level imports the way `test_no_pyside6_in_hardware_layer` does,
      or the pure functions live in a Qt-free submodule that test checks).

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments

2026-10-08: Added `src/rbl/services/cup_log.py` (`CupLog`, `CupLogKind`, `RestartChoice`,
`ContinueChoice`, `CupView`) and `src/rbl/services/cup_log_totals.py` (Qt-free
`read_dose_totals`, `find_previous_session_cup_log`, re-exported from `cup_log`). The pure
functions are in their own module so the no-Qt criterion is checkable by an AST test.
All criteria are covered by `tests/test_cup_log.py` (totals tests use files written by a
real `CupSessionWriter`). `insertion_count` = counted rows in the file plus the count in a
continued log's `# continued totals` header. Mutation check: treating every summary row as
counted turned 4 tests red. No bench verification needed.
