# 59: A one-shot script archives every store written with the old current scale

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/current-monitor-scale/spec.md` (Part H)
**Binding:** `docs/adr/0007-current-monitor-scale-is-2-ma-per-volt.md`, `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:**
- `src/rbl/config/paths.py`: `LOG_ROOT`, `DATA_DIR`, `CONFIG_DIR`, `TRIP_HISTORY_PATH`,
  `CONDITIONING_HISTORY_PATH`, `DYNAMIC_ADJUSTMENT_HISTORY_PATH`, `CALIBRATION_DIR`,
  `CHARACTERIZATION_DIR`, `LOAD_CAL_STORE`.
- `tests/test_check_tests_first.py`, lines 12-30: how a test imports a module from
  `scripts/`. Copy that pattern.
- `docs/hardware/eel5000-manufacturer-notes.md`, section 6, item 6.

## What to build

Everything RBL wrote about amplifier current before ADR 0007 is 5x too high. The operator
will run this script once on the beamline PC. It moves those files aside with a note
explaining why. It never deletes and never rescales anything.

**New file `scripts/archive_pre_adr_0007.py`:**

1. **A pure core.** `plan_archive(log_root: Path, config_dir: Path) -> list[tuple[Path, Path]]`
   returns (source, destination) pairs, only for sources that exist. It is pure: it
   takes its two roots as arguments and reads no `Path.home()` or environment variable.
   This is a requirement, so tests can drive it with temp folders.
2. **Data stores.** Relative to `log_root / "data"`, these move into
   `log_root / "data" / "archive" / "pre-2026-10-07-current-scale" / <same name>`:
   - `trip_history.jsonl`
   - `dynamic_adjustment_history.jsonl`
   - `conditioning_history.jsonl`
   - the folders `calibration/` and `load_characterization/`
3. **Config store.** `config_dir / "load_calibration.json"` moves into
   `config_dir / "archive" / "pre-2026-10-07-current-scale" / "load_calibration.json"`.
4. **Spike files.** Every `spikes.csv` anywhere under `log_root` is renamed in its own
   folder to `spikes.pre-adr-0007.csv`, and a sibling `spikes/` folder becomes
   `spikes.pre-adr-0007/`. Anything already under an `archive` folder is skipped.
5. **README.** `apply_archive(pairs, readme_dirs)` creates each archive folder, moves each
   pair, and writes `README.txt` in each archive folder. The README text is exactly:
   `Computed with the EEL5000 manual's 1 V = 10 mA current-monitor scale. The manufacturer says the scale is 1 V = 2 mA (docs/adr/0007-current-monitor-scale-is-2-ma-per-volt.md). Every current, capacitance and current threshold in these files is 5x too high. Deliberately not rescaled.`
6. **Command line.** `main(argv)`:
   - `--log-root` defaults to `rbl.config.paths.LOG_ROOT`; `--config-dir` defaults to
     `rbl.config.paths.CONFIG_DIR`; `--apply` is a flag.
   - **Without `--apply`:** print one line per pair, `would move <src> -> <dst>`, then
     `dry run: nothing moved; run again with --apply`. Return 0.
   - **With `--apply`:**
     - If either archive folder already exists, print
       `already archived: <folder>; refusing to run twice` and return 1, moving nothing.
     - If any destination exists, refuse the whole run the same way, before moving
       anything.
     - Otherwise move, then print `moved <src> -> <dst>` per pair. Return 0.
7. **Moves only.** Every move uses `Path.rename` or `shutil.move`. Nothing calls
   `unlink`, `rmtree`, `remove` or opens a file for writing, except `README.txt`.

## Guardrails

- The script never runs on import, and tests never call `main` without explicit
  `--log-root` and `--config-dir` pointing at `tmp_path`.
- Nothing under `src/rbl/` changes in this ticket.

## Acceptance criteria

- [x] **Dry run.** `test_dry_run_moves_nothing` (new, `tests/test_archive_pre_adr_0007.py`):
  - setup: every listed store plus a nested `sessions/s1/spikes.csv` and a `spikes/`
    folder, under temp roots;
  - result: `main([... no --apply])` returns 0, every file is still in place, and the
    output contains `would move` and `dry run: nothing moved`.
- [x] **Apply.** `test_apply_moves_exactly_the_listed_stores_and_writes_both_readmes` (new):
  - after `--apply`, each store is at its archive destination with byte-identical content;
  - `sessions/s1/spikes.pre-adr-0007.csv` and `sessions/s1/spikes.pre-adr-0007/` exist;
  - both `README.txt` files contain `5x too high`;
  - an unrelated file `data/vacuum/v.csv` has not moved.
- [x] **Second run.** `test_a_second_apply_refuses_and_changes_nothing` (new): a second
  `--apply` returns 1, prints `refusing to run twice`, and the directory listing is
  identical before and after.
- [x] **Missing stores.** `test_missing_stores_are_skipped_not_errors` (new): with only
  `trip_history.jsonl` present, `--apply` moves just it and returns 0.
- [x] **Pure core.** `test_plan_archive_reads_only_its_arguments` (new): with
  `Path.home` monkeypatched to raise, `plan_archive(tmp_log, tmp_cfg)` still returns the
  expected pairs.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments

### 2026-10-07: Implemented and verified
- Implemented `scripts/archive_pre_adr_0007.py` with pure `plan_archive(log_root, config_dir)`, `apply_archive(pairs, readme_dirs)`, and CLI `main(argv)` supporting dry-run by default and `--apply`.
- Stores moved: `trip_history.jsonl`, `dynamic_adjustment_history.jsonl`, `conditioning_history.jsonl`, `calibration/`, `load_characterization/` under `data/archive/pre-2026-10-07-current-scale/`; `load_calibration.json` under `config/archive/pre-2026-10-07-current-scale/`; nested `spikes.csv` and `spikes/` renamed to `spikes.pre-adr-0007.csv` and `spikes.pre-adr-0007/`. Both archive folders receive `README.txt` citing ADR 0007.
- Added comprehensive unit tests in `tests/test_archive_pre_adr_0007.py` covering all criteria:
  - `test_dry_run_moves_nothing`: dry run output and no file movements
  - `test_apply_moves_exactly_the_listed_stores_and_writes_both_readmes`: destination contents, spikes rename, README text, untouched unrelated files
  - `test_a_second_apply_refuses_and_changes_nothing`: refusal with code 1 and unchanged filesystem
  - `test_missing_stores_are_skipped_not_errors`: graceful handling of subsets of stores
  - `test_plan_archive_reads_only_its_arguments`: pure core with monkeypatched `Path.home`
  - `test_apply_archive_function_directly`: direct test of `apply_archive`
- Bench verification needed: Operator runs `python scripts/archive_pre_adr_0007.py --apply` once on the beamline PC when ready.
