# 169: Picoammeter connect errors show one short line, full text on hover

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/cup-status-fix/spec.md` (D5)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`
**Read first:** `visa_error_hint` and `_HINTS` in `src/rbl/hardware/visa_hints.py`; `_set_disconnected_view`, `on_cup_error` and `on_cup_connected` (it clears `_last_error`) in `src/rbl/gui/faraday_cup_tab.py`; `tab_after_connect_failure` and `CONNECT_FAIL_MSG` in `tests/test_faraday_cup_tab.py`; `tests/test_visa_hints.py`.

## What to build

When the Keithley 6482 fails to connect, the whole VISA error plus its operator hint goes
into `lbl_detail`, one red line wider than the tab, while the operator may only be moving
the cup. After this ticket the label shows a short reason and the full message is the
label's tooltip:

- New pure function `visa_short_reason(message: str) -> str` in
  `src/rbl/hardware/visa_hints.py`, beside `visa_error_hint`, same first-match-wins order:
  `VI_ERROR_LIBRARY_NFOUND` → `GPIB library not found on this PC`;
  `VI_ERROR_RSRC_NFOUND` → `nothing answered at this GPIB address`;
  `VI_ERROR_TMO` → `instrument did not answer in time`;
  `VI_ERROR_RSRC_BUSY` → `another program is holding the instrument`;
  anything else → the message's first line, and if that is longer than 80 characters, its
  first 79 characters followed by `…` (U+2026). It is pure (no Qt, no pyvisa) so it is
  tested with literal strings.
- `_set_disconnected_view` with a stored error: text `Not connected — <short reason>`,
  tooltip the full message. `on_cup_error`: text `Error: <short reason>`, tooltip the full
  message. Every other text `lbl_detail` shows sets its tooltip to `""`.

## Acceptance criteria

- [ ] Test `test_short_reason_for_each_known_visa_error`, in `tests/test_visa_hints.py`:
      each of the four status names inside a realistic literal message returns its exact
      phrase above.
- [ ] Test `test_short_reason_for_unknown_error_is_first_line_clipped`, in
      `tests/test_visa_hints.py`: `"short failure\nsecond line"` returns `short failure`; a
      single line of 120 `x` characters returns 79 `x` followed by `…` (length 80).
- [ ] Test `test_connect_failure_shows_short_reason_and_full_text_on_hover`, in
      `tests/test_faraday_cup_tab.py`: after `tab_after_connect_failure`, `lbl_detail.text()`
      is `Not connected — GPIB library not found on this PC` and `lbl_detail.toolTip()` is
      `CONNECT_FAIL_MSG`. Rewrite test_connect_failure_reason_survives_disconnect_event in
      place, same name, to assert that same short text instead of the full message. Place
      the new tests directly after test_connect_click_replaces_stale_failure_reason.
- [ ] Test `test_worker_error_shows_short_reason_and_full_text_on_hover`, in
      `tests/test_faraday_cup_tab.py`: `tab.on_cup_error(CONNECT_FAIL_MSG)` gives text
      `Error: GPIB library not found on this PC` and tooltip `CONNECT_FAIL_MSG`.
- [ ] Test `test_successful_connect_clears_error_tooltip`, in
      `tests/test_faraday_cup_tab.py`: after `tab_after_connect_failure`, then
      `on_cup_connected(...)` and `on_cup_disconnected()`, `lbl_detail.text()` is
      `Not connected` and `lbl_detail.toolTip()` is `""`.

**What tests may fake:** nothing beyond what `tab_after_connect_failure` already uses (a
real `Beamline`, its public signals emitted by hand).

**Out of scope:** the hint text itself and where the worker appends it; the cup actuation
panel; any other tab's error labels.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_visa_hints.py tests/test_faraday_cup_tab.py
```

Run pytest on the touched test files only (implement-ticket-parallel, section 4). CI runs the full suite on the PR.

## Comments
