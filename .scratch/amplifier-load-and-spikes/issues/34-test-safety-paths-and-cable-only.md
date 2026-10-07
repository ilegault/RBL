# 34: Test safety for new stores, their paths, and the cable-only load condition

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/amplifier-load-and-spikes/spec.md` (Part A)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `tests/conftest.py` (`_never_touch_the_real_calibration_store` - copy its pattern and its docstring's reasoning), `src/rbl/config/paths.py` (`CONFIG_DIR`, `DATA_DIR`, `LOAD_CAL_STORE`), `src/rbl/config/calibration_config.py` (`LoadCondition`), `src/rbl/gui/calibration_tab.py` (`_PreRunChecklistDialog`), `src/rbl/gui/load_characterization_tab.py` (`__init__`, `_selected_load_condition`).

## What to build

Every later ticket in this set writes to two new places. Before any of them exist, the
suite must be unable to write to the operator's real copies: a test once wrote a fabricated
capacitance into the real store, and the planner then called it "measured".

- `paths.py` gains `AMPLIFIER_ASSIGNMENTS_STORE = CONFIG_DIR / "amplifier_assignments.jsonl"`
  and `CHARACTERIZATION_DIR = DATA_DIR / "load_characterization"`.
- `tests/conftest.py` gains one autouse fixture per path, in the same shape as
  `_never_touch_the_real_calibration_store`, patching the attribute on `rbl.config.paths`
  to a path under `tmp_path`. Modules created later (tickets 35, 36) must read these paths
  through `rbl.config.paths` at call time (`paths.AMPLIFIER_ASSIGNMENTS_STORE`), not copy them
  into a module constant at import, so this fixture covers them. Say so in the fixture's
  docstring.
- `LoadCondition` gains `CABLE_ONLY = "CABLE_ONLY"` (HV cable attached, far end open).
- The Load Characterization tab offers a third radio button "Cable only (far end open)";
  `_selected_load_condition` returns `LoadCondition.CABLE_ONLY` for it.
- `_PreRunChecklistDialog` shows a checklist item for `CABLE_ONLY` telling the operator to
  confirm the cable's far end is open and insulated.

Tests may fake: nothing on disk; Qt widgets are real (offscreen).

## Acceptance criteria

- [ ] Inside any test, `rbl.config.paths.AMPLIFIER_ASSIGNMENTS_STORE` and `CHARACTERIZATION_DIR` are both under `tmp_path` and not under `Path.home()` (one test asserts both).
- [ ] `LoadCondition('CABLE_ONLY')` round-trips, and `[c.value for c in LoadCondition]` is `['DISCONNECTED', 'ON_PLATES', 'CABLE_ONLY']`.
- [ ] On the Load Characterization tab, checking the new radio button makes `_selected_load_condition()` return `LoadCondition.CABLE_ONLY`; `test_disconnected_condition_selection` still passes unchanged.
- [ ] `_PreRunChecklistDialog(LoadCondition.CABLE_ONLY)` contains a checklist item whose text includes `far end`.
- [ ] All existing tests pass unchanged.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
