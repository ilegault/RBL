# 170: Connect the shared LabJack from the Faraday Cup tab

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/cup-status-fix/spec.md` (D6)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `docs/adr/0003-commanded-and-confirmed-cup-position.md`
**Read first:** `LabJackPanel` in `src/rbl/gui/widgets/connection_bar.py`; how `LogAmpTab` in `src/rbl/gui/logamp_tab.py` builds `self.lj_panel`, disables it when `LJM_AVAILABLE` is false, and implements `on_labjack_connected` / `on_labjack_disconnected`; `LabJackConsumer` in `src/rbl/gui/labjack_consumer.py`; `TAB_DECLARATIONS`, `stream_consuming_tabs`, the `for tab in self._lj_tabs:` wiring loop and `_labjack_connect_if_needed` in `src/rbl/gui/app.py`; the `act_box` ("Cup Actuation") construction in `FaradayCupTab._build_ui` (search `QGroupBox("Cup Actuation")`) in `src/rbl/gui/faraday_cup_tab.py`; the `win` fixture in `tests/test_gui_hardware.py`.

## What to build

Cup actuation needs the T7, but the T7's Connect button lives only on the Overview and the
five LabJack tabs. After this ticket the Faraday Cup tab has the same `LabJackPanel`, as the
first widget inside its "Cup Actuation" box, driving the one shared T7 exactly as the other
panels do. There is still one T7 handle, owned by `Beamline` (a second `ljm` open from a tab
causes transaction conflicts).

- `FaradayCupTab` builds `self.lj_panel = LabJackPanel()` and adds it first in `act_lay`;
  when `LJM_AVAILABLE` is false it calls `self.lj_panel.set_enabled(False)`, as `LogAmpTab`
  does.
- `FaradayCupTab` implements the four `LabJackConsumer` methods: `on_labjack_connected(serial)`
  calls `self.lj_panel.set_connected(True, serial)`; `on_labjack_disconnected()` and
  `on_labjack_error(msg)` call `self.lj_panel.set_connected(False)`; `on_profile_changed`
  does nothing. Actuation state still comes only from `cup_actuation_changed`.
- In `src/rbl/gui/app.py`, the "Faraday Cup" `TabDeclaration` gets `consumes_stream=True`, so
  the existing loop wires its panel's `connect_requested` / `disconnect_requested` to
  `_labjack_connect` / `_labjack_disconnect` and fans the lifecycle calls out to it. "Slit
  Currents" is declared earlier, so `_lj_tabs[0]` (read by `_labjack_connect_if_needed`) is
  unchanged.

## Acceptance criteria

- [ ] Test `test_cup_tab_connect_button_opens_the_shared_t7`, in `tests/test_gui_hardware.py`:
      with `win.beamline.connect_labjack` monkeypatched to record its arguments, set
      `win.faraday_cup_tab.lj_panel.le_ident` to `470012345` and click its `btn_conn`;
      exactly one call is recorded and its identifier is `470012345`.
- [ ] Test `test_cup_tab_panel_follows_the_shared_connection`, in `tests/test_gui_hardware.py`:
      `win._on_labjack_connected("470012345")` makes the cup tab's `lj_panel.btn_conn.text()`
      `Disconnect` and `lbl_serial.text()` `T7 serial #470012345`;
      `win._on_labjack_disconnected()` returns `btn_conn.text()` to `Connect`.
- [ ] Test `test_cup_tab_is_a_labjack_consumer`, in `tests/test_gui_hardware.py`:
      `isinstance(win.faraday_cup_tab, LabJackConsumer)` is true.
- [ ] Test `test_cup_tab_labjack_panel_sits_in_cup_actuation_box`, in
      `tests/test_gui_hardware.py`: walking `win.faraday_cup_tab.lj_panel`'s `parent()`
      chain reaches a `QGroupBox` whose `title()` is `Cup Actuation`.
- [ ] test_stream_consuming_tabs_derived_from_declaration and the rest of
      `tests/test_gui_hardware.py` still pass unchanged. (no test: CI's full suite)

**What tests may fake:** `Beamline.connect_labjack` (no real T7). The `MainWindow`, its
wiring and the panels are real.

**Out of scope:** the Overview's Connect All; stream profiles; the interlock control (ticket 172).

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_gui_hardware.py
```

Run pytest on the touched test files only (implement-ticket-parallel, section 4). CI runs the full suite on the PR.

## Comments
