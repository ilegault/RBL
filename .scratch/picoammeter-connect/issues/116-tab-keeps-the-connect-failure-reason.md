# 116: The Faraday Cup tab keeps showing why the picoammeter is not connected

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/faraday-cup-reader/spec.md`
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

## What to build

When the picoammeter fails to connect, the reason never stays on screen. The operator
on 2026-11-10 had to read the console to see `VI_ERROR_LIBRARY_NFOUND`.

Cause, in order: `PicoammeterWorker._connect_driver` emits `error(msg)` and then
`connected_changed(False, "")`. `Beamline` relays these as `cup_error` and then
`cup_disconnected_evt` (`_on_picoammeter_error`, `_on_picoammeter_connected_changed` in
`src/rbl/state/picoammeter_link.py`). `MainWindow` wires them to
`FaradayCupTab.on_cup_error` and `FaradayCupTab.on_cup_disconnected`
(`src/rbl/gui/app.py`, the three `self.beamline.cup_*` connections). `on_cup_error` writes
`"Error: <msg>"` into `lbl_detail`, and a moment later `on_cup_disconnected` calls
`_set_disconnected_view()`, which overwrites it with `"Not connected"`.

After this ticket the tab remembers the last error until something makes it stale, and
the disconnected view shows it.

## Acceptance criteria

- [ ] `FaradayCupTab` holds `self._last_error: str | None` (initialised `None` in
      `__init__` before the first `_set_disconnected_view()` call). `on_cup_error(msg)`
      stores `msg` there and keeps its current label update. `_set_disconnected_view()`
      sets `lbl_detail` to `f"Not connected — {self._last_error}"` with the `theme.FAULT`
      style when `_last_error` is set, and to `"Not connected"` with the existing
      `theme.MUTED` style when it is `None`. Test:
      tests/test_faraday_cup_tab.py::`test_connect_failure_reason_survives_disconnect_event`
      — real `Beamline` and tab; connect `beamline.cup_error` → `tab.on_cup_error` and
      `beamline.cup_disconnected_evt` → `tab.on_cup_disconnected` exactly as `app.py`
      does; call `beamline._on_picoammeter_error(MSG)` then
      `beamline._on_picoammeter_connected_changed(False, "")` with the literal
      `MSG = "Failed to connect to Keithley 6482 on GPIB0::14::INSTR: VI_ERROR_LIBRARY_NFOUND (-1073807202): A code library required by VISA could not be located or loaded."`;
      assert `tab.lbl_detail.text() == "Not connected — " + MSG` and
      `"Disconnected" in tab.status_pill.text()`.
- [ ] A successful connect clears the remembered error: `on_cup_connected()` sets
      `self._last_error = None`. Test:
      tests/test_faraday_cup_tab.py::`test_successful_connect_clears_failure_reason` —
      after the sequence above, call `tab.on_cup_connected("KEITHLEY INSTRUMENTS INC.,MODEL 6482")`
      then `tab.on_cup_disconnected()`; assert `tab.lbl_detail.text() == "Not connected"`.
- [ ] Pressing the button clears it and says what is happening.
      `_on_connect_clicked()` sets `self._last_error = None` on both branches. On the
      connect branch it then sets `lbl_detail` to `f"Connecting to {resource}…"` (the
      character is U+2026) with the `theme.MUTED` style, before calling
      `self.beamline.connect_picoammeter(resource)`. Test:
      tests/test_faraday_cup_tab.py::`test_connect_click_replaces_stale_failure_reason` —
      after the error sequence above, monkeypatch `beamline.connect_picoammeter` to a
      no-op recorder (as `test_connect_if_needed` does), set `tab.le_resource` to
      `"GPIB0::2::INSTR"`, call `tab.btn_connect.click()`; assert
      `tab.lbl_detail.text() == "Connecting to GPIB0::2::INSTR…"`.
- [ ] Existing tab tests are not edited and pass, in particular the initial-state test
      that asserts `"Not connected" in tab.lbl_detail.text()`. Confirm with
      `git diff tests/test_faraday_cup_tab.py` that only the three new tests were added.
      (by hand)
- [ ] The `_set_disconnected_view` docstring states that the last failure reason is shown
      until a successful connect or an operator click, and why (the error/disconnect
      ordering above). (by hand)

**What tests may fake:** `beamline.connect_picoammeter` in the third test only. The tab,
`Beamline` and the signal relays are real; the worker is never started.

**Out of scope:** the content of the error message, including any hint (ticket 117).
Retargeting the worker (ticket 115). Any change to `app.py`. A separate error label or
dialog.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
