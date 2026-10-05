# 12: Edits on the tab reach the detector, the writer and the session file

**Blocked by:** 04, 06, 07, 11

**Status:** ready-for-agent

**Read first:** `src/rbl/gui/faraday_cup_tab.py` - the constructor where
`self.acquisition = CupAcquisitionStateMachine(detector=AuthorityDetector())` and
`self.session_writer` are built, and the Sampling Cycle group box construction below
which the new group goes. `AGENTS.md` section 7 and the repo's edit discipline: new
widgets are standalone files and `faraday_cup_tab.py` receives minimal, anchored
edits.

**What to build:** The first version an operator can actually use. The Acquisition
Settings group appears on the Faraday Cup tab; typing a new arm threshold changes the
threshold the detector compares against, typing a new settle window changes which
samples the post-settle statistics use, and each change is recorded in the session
file. Values still return to their defaults when the application restarts - ticket 13
adds persistence.

- [ ] `AcquisitionSettingsGroup` is constructed in `FaradayCupTab.__init__` and added
      to the layout directly below the Sampling Cycle group box. A test asserts the
      widget exists as an attribute of the tab and that the tab still builds with no
      hardware connected - every tab in this application must work with nothing
      connected.
- [ ] The tab connects `settings_changed` to a handler that, for key
      `arm_threshold_a` or `release_threshold_a`, calls `set_thresholds` on
      `self.acquisition.detector` with both current values, and for key
      `settle_window_s` calls `self.session_writer.set_settle_window`. A test emits
      each key and asserts the detector attributes and the writer's settle window hold
      the new numbers.
- [ ] The same handler calls `self.session_writer.write_settings_changed` once per
      change with the key, old value and new value. A test emits one threshold change
      and asserts exactly one row appears in the CSV carrying both values.
- [ ] One end-to-end test proves the edit reached the thing that decides: using
      `tests/payloads.py` to drive real stream windows through a real `Beamline` as
      the repo requires for anything that puts a value on screen, lower the arm
      threshold through the widget, feed a current above the new threshold and below
      the old one, and assert a run opens **and** that the run's opening row records
      the new threshold. If the row shows the old number, ticket 04's fix is not
      wired up.
- [ ] `MainWindow` is not modified and no cross-tab connection is added. This feature
      is contained in the Faraday Cup tab. A reviewer should see a diff in
      `faraday_cup_tab.py` of roughly a constructor line, a layout line and one
      handler.

**Tests may fake:** the output directory via `tmp_path`, and the hardware, which
`tests/payloads.py` already stands in for. The detector, the state machine and the
session file must be real.

**Out of scope:** loading or saving the settings file (ticket 13), locking the fields
(ticket 14), period and dwell (ticket 15).
