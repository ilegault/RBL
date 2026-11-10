# 117: Picoammeter VISA errors carry a plain-language hint

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/faraday-cup-reader/spec.md`
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

## What to build

The picoammeter's connect errors are raw VISA status names. On 2026-11-10 the lab PC
showed `VI_ERROR_LIBRARY_NFOUND (-1073807202): A code library required by VISA could not
be located or loaded.`, which does not tell an operator that the likely fix is installing
Keysight IO Libraries Suite on that PC. Each of the four errors this GPIB chain has
actually produced (see `.scratch/faraday-cup-reader/issues/01-keysight-visa-bench-verification.md`,
Comments) has a known operator action.

After this ticket every error the picoammeter worker emits ends with a one-sentence hint
when the VISA status is one of those four. The hint travels inside the existing `error`
signal string, so the log, `Beamline.cup_error` and the Faraday Cup tab all carry it with
no other change.

The lookup is a **pure function in the hardware layer**, outside the worker thread, so it
is tested with literal strings and no VISA, no Qt, no thread.

## Acceptance criteria

- [ ] New module `src/rbl/hardware/visa_hints.py` with a `WHY THIS EXISTS` docstring and
      `def visa_error_hint(message: str) -> str | None`. It returns the hint for the first
      of these substrings found in `message`, checked in this order, else `None`. The
      hint strings are exactly:
      `"VI_ERROR_LIBRARY_NFOUND"` → `"Hint: this PC's VISA could not load its GPIB library. Check that Keysight IO Libraries Suite is installed on this PC and the 82357B adapter is plugged into it, then rescan in Keysight Connection Expert."`;
      `"VI_ERROR_RSRC_NFOUND"` → `"Hint: nothing answered at this address. Rescan in Keysight Connection Expert and check the address on the 6482 front panel (MENU > COMMUNICATION > GPIB)."`;
      `"VI_ERROR_TMO"` → `"Hint: the instrument did not answer in time. Check the 6482 is powered, set to GPIB (not RS-232), not inside a front-panel menu, and set to this address."`;
      `"VI_ERROR_RSRC_BUSY"` → `"Hint: another program is holding this instrument. Close Keysight Connection Expert, NI MAX and any other copy of RBL."`.
      The module imports nothing from Qt, `pyvisa` or `rbl.state`.
      Test: tests/test_visa_hints.py::`test_each_known_visa_error_gets_its_hint` — one
      test looping over four **literal** messages copied from real output, e.g.
      `"Failed to connect to Keithley 6482 on GPIB0::14::INSTR: VI_ERROR_LIBRARY_NFOUND (-1073807202): A code library required by VISA could not be located or loaded."`
      and `"VI_ERROR_RSRC_BUSY (-1073807246): The resource is valid, but VISA cannot currently access it."`,
      asserting each returns a string starting `"Hint: "` that contains, respectively,
      `"Keysight IO Libraries Suite"`, `"MENU > COMMUNICATION > GPIB"`, `"not inside a front-panel menu"`, `"Close Keysight Connection Expert"`.
- [ ] Unknown text gets no hint. Test:
      tests/test_visa_hints.py::`test_unknown_error_has_no_hint` — `visa_error_hint("Keithley 6482 read error: could not convert string to float")`
      and `visa_error_hint("")` both return `None`.
- [ ] `PicoammeterWorker._connect_driver` and the read-error branch of
      `PicoammeterWorker.run` in `src/rbl/hardware/picoammeter_worker.py` append
      `" " + hint` to the message they already build, when `visa_error_hint(msg)` is not
      `None`, before logging and emitting it. Test:
      tests/test_picoammeter_worker.py::`test_connect_failure_message_carries_hint` —
      patch `rbl.hardware.picoammeter_worker.Keithley6482` with
      `side_effect=Exception("VI_ERROR_LIBRARY_NFOUND (-1073807202): A code library required by VISA could not be located or loaded.")`,
      patch `CUP_RECONNECT_BACKOFF_S` to `[0.01]` as
      `test_worker_handles_read_exception` does, start the worker on `"GPIB0::2::INSTR"`,
      wait for the first `error` emission; it contains both `"VI_ERROR_LIBRARY_NFOUND"`
      and `"Keysight IO Libraries Suite"`.
- [ ] The existing test tests/test_picoammeter_worker.py::`test_worker_handles_read_exception`
      is not edited and still passes (its error has no VISA status, so no hint is
      appended and its substring assertion holds).
- [ ] The `picoammeter_worker.py` module docstring's "MISSING HARDWARE AND RECONNECTION"
      section says error messages carry a hint from `visa_hints.visa_error_hint`. (by hand)

**What tests may fake:** `Keithley6482` and `CUP_RECONNECT_BACKOFF_S` in the worker test.
`visa_error_hint` is never patched.

**Out of scope:** hints for the function generators, scope or LabJack. How the tab
displays the error (ticket 116). Translating status codes by number. Any change to
`keithley6482_driver.py`.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
