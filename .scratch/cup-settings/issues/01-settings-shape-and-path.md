# 01: The acquisition settings shape and its file path

**Blocked by:** None (can start immediately)

**Status:** done

**Read first:** `.scratch/cup-settings/spec.md`, section "The settings store is a pure
module with no Qt". `AGENTS.md` section 2 (the layering rule: `rbl/config/` is numbers
only, no I/O, no Qt, no state). `docs/adr/0002-cup-acquisition-triggered-by-current.md`,
the amendment dated 2026-09-24.

**What to build:** A description of the seven acquisition settings as one value, and
the canonical path of the file they will live in. Nothing reads or writes that file in
this ticket. Afterwards any module can say "the settings in force" and mean one object
with known field names, instead of importing seven constants.

**Copy the style of:** `src/rbl/config/load_calibration_store.py` - its module
docstring, its `WHY` sections, and the way it takes its path from
`rbl/config/paths.py` rather than building one.

- [x] `src/rbl/config/cup_settings_store.py` (new) defines a frozen dataclass
      `CupSettings` with exactly these seven float fields, spelled exactly this way:
      `arm_threshold_a`, `release_threshold_a`, `settle_window_s`, `cycle_period_s`,
      `cycle_dwell_s`, `arm_debounce_s`, `release_interval_s`. A test asserts the set
      of field names from `dataclasses.fields(CupSettings)` equals that set exactly,
      and that assigning to a field raises `dataclasses.FrozenInstanceError`.
- [x] `CupSettings.defaults()` (a `@staticmethod` or `@classmethod`) returns an
      instance whose seven fields equal, in order, `CUP_ARM_THRESHOLD_A`,
      `CUP_RELEASE_THRESHOLD_A`, `CUP_SETTLE_WINDOW_S`, `CUP_CYCLE_PERIOD_S`,
      `CUP_CYCLE_DWELL_S`, `CUP_ARM_DEBOUNCE_S`, `CUP_RELEASE_INTERVAL_S` imported
      from `rbl.config.cup_config`. A test asserts all seven equalities by importing
      the constants itself. **No default value may appear as a literal in the new
      module**: `cup_config.py` stays the one place a default is written down, which
      is the "every number has one home" rule in `AGENTS.md` section 2.
- [x] `src/rbl/config/paths.py` gains `CUP_SETTINGS_STORE: Path = CONFIG_DIR /
      "cup_settings.json"` on the line after `LOAD_CAL_STORE`. A test asserts
      `CUP_SETTINGS_STORE.name == "cup_settings.json"` and
      `CUP_SETTINGS_STORE.parent == CONFIG_DIR`.
- [x] The new module imports nothing from PySide6, calls no clock
      (`time.time`, `time.monotonic`), and performs no file I/O. A test asserts the
      module's source contains no `import PySide6`, no `from PySide6`, and no `open(`.
      File I/O arrives in ticket 03; putting it here breaks the layering rule.
- [x] The module docstring carries a `WHY THIS EXISTS` section stating the reason
      these settings live outside `cup_config.py`: on the control PC the application
      is a PyInstaller build, so a constant cannot be changed without a rebuild, and
      an arm threshold that cannot be changed at the bench silently produces zero
      acquisition runs when the beam is weaker than it.

**Tests may fake:** nothing, and there is nothing to fake. No test in this ticket
touches the filesystem or `~/.config/rbl/`.

**Out of scope:** validation (ticket 02), reading or writing the file (ticket 03), any
change to `cup_config.py`'s values, any GUI.

## Comments

### Landed 2026-10-05
- Defined `CupSettings` frozen dataclass with seven float fields and `@classmethod defaults()` in `src/rbl/config/cup_settings_store.py`.
- Added `CUP_SETTINGS_STORE` path in `src/rbl/config/paths.py`.
- Verified purity: no Qt, no clocks, no file I/O, no default numeric literals in the store module.
- Added comprehensive unit tests in `tests/test_cup_settings_store.py` covering all criteria.
- Full gate passed (ruff check clean, type_gate ratchet held, check_tests_first compliant, 1932/1932 pytest tests passed).

