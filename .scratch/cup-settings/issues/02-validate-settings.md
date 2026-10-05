# 02: Validation of the acquisition settings, with one warning per rejected key

**Blocked by:** 01

**Status:** in-progress

**Read first:** `.scratch/cup-settings/spec.md`, section "The rules each value must
satisfy". `docs/adr/0002-...md` amendment decision A5 (a rejected value falls back to
its default, visibly, and never fails silently).

**What to build:** One pure function that turns a dictionary of raw values into a
`CupSettings` plus a list of warnings. It is the only place any rule about these
numbers is written. It is used both when the settings file is read and when an
operator commits an edit on the tab, so the two can never disagree about what is
allowed.

- [ ] `cup_settings_store.py` defines a frozen dataclass `SettingsLoadWarning` with
      exactly the fields `key: str`, `found: str`, `reason: str`, `fallback: float`,
      and a function `validate_settings(raw: dict) -> tuple[CupSettings,
      list[SettingsLoadWarning]]`. A test asserts the field names and that a returned
      warning for a bad `arm_threshold_a` has `key == "arm_threshold_a"`, a non-empty
      `reason`, and `fallback == CUP_ARM_THRESHOLD_A`.
- [ ] The rules are exactly these, and a value failing its rule is replaced by that
      field's default and produces one warning:
      `arm_threshold_a`: `1e-9 <= v <= 1e-3`.
      `release_threshold_a`: `0 < v < arm_threshold_a`.
      `cycle_period_s`: `v >= cycle_dwell_s + 2 * CUP_MOVE_CONFIRMATION_TIMEOUT_S`
      and `v <= 604800.0`.
      `cycle_dwell_s`: `v > settle_window_s` and
      `v + 2 * CUP_MOVE_CONFIRMATION_TIMEOUT_S < cycle_period_s`.
      `settle_window_s`: `0 <= v < cycle_dwell_s`.
      `arm_debounce_s`: `0 < v <= 60.0`.
      `release_interval_s`: `0 < v <= 60.0`.
      A test asserts each rule at the value just inside and just outside its bound.
      **The term `2 * CUP_MOVE_CONFIRMATION_TIMEOUT_S` is computed from that constant
      imported from `rbl.config.cup_config`, never written as `4.0`**: it is the
      insert and the retract move that must both fit inside one period.
- [ ] Fields are validated in exactly this order, and each cross-field rule compares
      against the values already accepted earlier in that order: `arm_threshold_a`,
      `release_threshold_a`, `cycle_dwell_s`, `cycle_period_s`, `settle_window_s`,
      `arm_debounce_s`, `release_interval_s`. A test asserts that a dict whose dwell
      and period are both invalid produces exactly two warnings and a `CupSettings`
      holding both defaults, rather than one rule masking the other.
- [ ] `validate_settings` never raises, for any input. A test passes a dict whose
      values are a string, `None`, `float("nan")`, `float("inf")`, a negative number,
      and a missing key, and asserts the call returns and every affected key carries
      its default and one warning. A missing key produces its default and **no**
      warning: absent is not the same as wrong.
- [ ] Keys in `raw` that are not one of the seven are ignored silently and produce no
      warning. A test asserts a dict with an extra `"_README"` key and seven valid
      values returns zero warnings.

**Tests may fake:** nothing. This function takes a dict and returns values; every test
here calls it directly. No Qt, no filesystem.

**Out of scope:** reading or writing the file, rendering warnings anywhere, and
changing `CupDetector`'s own `ValueError` when its release threshold is not below its
arm threshold (that stays exactly as it is).
