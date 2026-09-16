# 12: Bench — verify the wiring and the status decoding by hand

**What to build:** Nothing. This is bench verification, and it is the gate before any
software commands a real move.

**The first end-to-end test drives a real cup into a real beamline. This ticket is what
stops that being the first time anyone checks the polarity.**

**Blocked by:** 02, 03

**Status:** ready-for-developer

**An agent must not claim this ticket.**

## Why this is not optional

Cup OUT requires both contact closures, and the cup fails IN where it acts as a beam
stop. A polarity error on the enable line does not announce itself: it produces a cup
that appears to work and fails in the wrong direction, or a cup that sits in the beam
while the application believes it is out.

The `LJTick-RelayDriver` datasheet describes verifying the outputs against a resistor
before anything is connected to them. Do that first. The LJTRD is wired by flying leads
rather than plugged into a terminal block — its datasheet states the `VS` pin "is not
used by the LJTRD and thus is not connected to anything," so only `GND`, `IOA` and `IOB`
carry signal.

Confirm the relay board's `JD-VCC` jumper is removed and the coil supply comes from a
separate 5 V source, so the coil and contact side is not sharing the LabJack's supply on
a beamline that arcs at rough vacuum.

## What to check

- [ ] LJTRD `IOA` and `IOB` verified against a resistor per the datasheet, before either
      is connected to the relay board
- [ ] Output-high on the LJTRD closes its switch, pulls the relay board's active-low
      input down, and energises the relay — confirmed by observation, not assumed
- [ ] `JD-VCC` jumper removed; coil supply is a separate 5 V source, not the LabJack's
- [ ] With the cup moved **by hand** and nothing commanding it, the streamed `FIO_STATE`
      decodes correctly in all four states: cup IN, cup OUT, in transit mid-travel, and
      the AUTO contact asserted and not asserted
- [ ] A **closed** status contact is confirmed to read as bit value **0** on the actual
      hardware, matching the decode function's inversion
- [ ] Both relays de-energised leaves the cup IN, confirmed by observation
- [ ] Pulling power to the relay coils with the cup OUT returns it IN, confirmed by
      observation — this is the fail-safe and it is a property of the wiring
- [ ] Findings recorded under this ticket's `## Comments`, including any measured value
      that differs from what the spec assumed
