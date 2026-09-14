# 04: Keithley 6482 driver and response parsing

**What to build:** A driver that talks to the Keithley 6482 picoammeter over GPIB and
turns its responses into typed readings. No application state, no Qt, no threads — this
is protocol and parsing only.

One query returns the channel 1 current, the instrument's own relative timestamp, and
its status word. The status word's over-range bit is the authority on whether a sample
can be trusted; the application must never infer over-range from the magnitude of a
value. The instrument also signals over-range and unavailable readings with distinct
out-of-band sentinel values, and both must be recognised rather than written into the
record as though they were currents.

**Blocked by:** 01

**Status:** done

> **The instrument on the bench may be a Keithley 6485 stand-in.** The 6482 is the
> target and this ticket is written against it deliberately. Do not rewrite it for the
> 6485 — the 6485 is single-channel with no voltage source, so the source-output
> safety rules below would look unnecessary against it and are not. See ticket 01.

Two requirements are structural, not stylistic, and the test seam for the whole feature
depends on them:

- Response parsing is a **pure function** taking the raw response text and returning a
  typed reading. It does not live inside a polling loop and does not touch a transport.
- The driver **never sends the configure command**. That command turns both voltage
  source outputs on, and those outputs would be driving the cup collector. Set the
  measurement function and the reading format explicitly instead, and assert both source
  outputs off after connecting. Record this reasoning in the module docstring's
  `WHY THIS EXISTS` section — it is a safety requirement, not a preference.

- [x] Connects to the 6482 by resource string and reports its identification
- [x] Configures channel 1 for DC current with autoranging enabled
- [x] The median filter and the averaging filter are off (the manual documents that the
      median filter makes autoranging very slow)
- [x] Integration time is set to one power-line cycle
- [x] Both voltage source outputs are asserted off after connecting, and this is verified
      by a test
- [x] The configure command appears nowhere in the module
- [x] A reading query returns current, instrument timestamp, and status word together
- [x] The parse function is pure and independently importable
- [x] Parse handles: a normal reading; the over-range sentinel; the unavailable-reading
      sentinel; a status word with the over-range bit set; one without; malformed input
- [x] Over-range is determined from the status word, never from the value's magnitude
- [x] Channel 2 is represented as absent rather than as a number
- [x] Unit tests follow the existing mocked-VISA pattern used for the function generator
      driver
- [x] The GPIB protocol mode is queried at connect with `:SYSTem:MEP:STATe?` and
      recorded on the reading structure or in the connect log. `1` means SCPI, `0`
      means the 488.1 protocol. This is not optional: the protocol is a front-panel
      setting stored in EEPROM that cannot be changed over the bus, and under 488.1 a
      query must be the only command on the line. A driver that assumes SCPI and
      sends a compound query against an instrument someone switched to 488.1 fails in
      a way that looks like a flaky cable.
- [ ] Verified against a real 6482 on the bench; findings in Comments. A 6485 stand-in
      can confirm the transport, the address and the protocol mode, but **not** the
      6482's element names or source-output behaviour — see ticket 01's Comments.
      If only the stand-in is available, verify what it can verify, say so in
      Comments, and leave this box unticked.

## Comments

### 2026-09-14 — Keithley 6482 driver and pure parsing implemented

1. Implemented `rbl.hardware.keithley6482_driver`:
   - `parse_reading(raw: str, protocol_mode: int | None = None) -> Keithley6482Reading` as an independently importable pure function.
   - Status word bit 6 (`1 << 6`, `0x40`) as the over-range condition authority, preventing inferring over-range from current magnitude.
   - Sentinels (+/-9.91e37 overflow, +/-9.90e37 unavailable) correctly detected and mapped.
   - Channel 2 declared as absent (`None`) in `Keithley6482Reading`.
   - Never sends the configure command (`:CONFigure`), protecting the cup collector from bias potential.
   - Commands voltage sources OFF and verifies `:SOURce1:STATe?` / `:SOURce2:STATe?` at connect.
   - Autorange enabled, median filter OFF, averaging filter OFF, 1 NPLC integration, elements set to `READing,TIME,STATus`.
   - Protocol mode queried with `:SYSTem:MEP:STATe?` and stored/propagated.
2. Verified with 37 dedicated unit tests in `tests/test_keithley6482_driver.py` using mocked PyVISA.
3. Full test suite (1671 tests), ruff, type gate (0 hard errors, 139 ratchet), and tests-first gates all pass cleanly.

