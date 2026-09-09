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

**Status:** ready-for-agent

Two requirements are structural, not stylistic, and the test seam for the whole feature
depends on them:

- Response parsing is a **pure function** taking the raw response text and returning a
  typed reading. It does not live inside a polling loop and does not touch a transport.
- The driver **never sends the configure command**. That command turns both voltage
  source outputs on, and those outputs would be driving the cup collector. Set the
  measurement function and the reading format explicitly instead, and assert both source
  outputs off after connecting. Record this reasoning in the module docstring's
  `WHY THIS EXISTS` section — it is a safety requirement, not a preference.

- [ ] Connects to the 6482 by resource string and reports its identification
- [ ] Configures channel 1 for DC current with autoranging enabled
- [ ] The median filter and the averaging filter are off (the manual documents that the
      median filter makes autoranging very slow)
- [ ] Integration time is set to one power-line cycle
- [ ] Both voltage source outputs are asserted off after connecting, and this is verified
      by a test
- [ ] The configure command appears nowhere in the module
- [ ] A reading query returns current, instrument timestamp, and status word together
- [ ] The parse function is pure and independently importable
- [ ] Parse handles: a normal reading; the over-range sentinel; the unavailable-reading
      sentinel; a status word with the over-range bit set; one without; malformed input
- [ ] Over-range is determined from the status word, never from the value's magnitude
- [ ] Channel 2 is represented as absent rather than as a number
- [ ] Unit tests follow the existing mocked-VISA pattern used for the function generator
      driver
- [ ] Verified against the real instrument on the bench; findings in Comments
