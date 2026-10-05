# 13: Settings survive a restart, and a bad file says so

**Blocked by:** 03, 12

**Status:** ready-for-agent

**Read first:** `.scratch/cup-settings/spec.md`, "The settings store" and "Where the
numbers come from at start". `docs/adr/0002-...md` amendment decisions A3, A4 and A5.
`src/rbl/gui/faraday_cup_tab.py` as ticket 12 leaves it.

**What to build:** The settings an operator typed last week are the settings the
application starts with this week, and anything wrong in the stored file is named on
screen rather than quietly replaced. A save that fails says so too - a settings file
the operator believes was written and was not is the silent failure this whole
feature exists to avoid.

- [ ] `FaradayCupTab.__init__` calls `load_settings()` and applies the result: both
      thresholds to the detector via `set_thresholds`, the settle window to the
      session writer via `set_settle_window`, and `cycle_period_s` / `cycle_dwell_s`
      to the scheduler via `set_period` / `set_dwell`. A test writes a settings file
      with non-default values, builds the tab, and asserts all five landed in those
      four places.
- [ ] Every committed edit, from either the settings group or the reset button, calls
      `save_settings` after the change is applied. A test edits a threshold, then
      reads the file from disk and asserts the new value is in it; a second test
      builds a fresh tab against the same file and asserts the field shows the saved
      value.
- [ ] A `save_settings` returning `False` shows a visible message in the `FAULT` role
      naming the settings file path. A test monkeypatches the save to return `False`,
      makes an edit, and asserts the message is visible and names the path. The edit
      itself still applies to the detector: a failed save must not also lose the
      change for the current session.
- [ ] Warnings from `load_settings` are passed to the widget's
      `set_load_warnings` at construction, and a value differing from its
      `cup_config` default is indicated on the tab. A test builds the tab against a
      file with one invalid key and asserts the displayed text contains the key name,
      the value found, the reason, and the fallback.
- [ ] Every test in this ticket redirects the settings store path to `tmp_path` and
      none of them touch the operator's real `~/.config/rbl/cup_settings.json`. Use
      the redirect added in ticket 03; `tests/conftest.py` has an autouse fixture doing
      exactly this for the load-calibration store because a test once wrote a
      fabricated value into the operator's real store.

**Tests may fake:** the settings file path, the output directory, and a failing save
via monkeypatch. The load, the apply and the round-trip through a second tab instance
must be real.

**Out of scope:** locking (ticket 14), period and dwell UI behaviour (ticket 15), and
reading the file again while the application runs - the application owns the file and
never merges a hand edit made while it is open.
