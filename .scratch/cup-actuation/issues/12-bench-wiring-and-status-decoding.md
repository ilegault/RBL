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

- [x] LJTRD `IOA` and `IOB` verified against a resistor per the datasheet, before either
      is connected to the relay board
- [x] Output-high on the LJTRD closes its switch, pulls the relay board's active-low
      input down, and energises the relay — confirmed by observation, not assumed
- [x] `JD-VCC` jumper removed; coil supply is a separate 5 V source, not the LabJack's
- [ ] With the cup moved **by hand** and nothing commanding it, the streamed `FIO_STATE`
      decodes correctly in all four states: cup IN, cup OUT, in transit mid-travel, and
      the AUTO contact asserted and not asserted
- [x] A **closed** status contact is confirmed to read as bit value **0** on the actual
      hardware, matching the decode function's inversion
- [x] Both relays de-energised leaves the cup IN, confirmed by observation
- [ ] Pulling power to the relay coils with the cup OUT returns it IN, confirmed by
      observation — this is the fail-safe and it is a property of the wiring
- [ ] **Process killed with the cup OUT.** Command OUT from the RBL app, then end the
      RBL process from Task Manager (End task, not the window's close button: a clean
      close opens relay 1 on purpose and proves nothing). Record whether the cup returns
      IN. The spec and ADR 0003 decision 6 say a crash returns it IN. The T7 holds its
      digital outputs after the host process dies, so the expected result is that the
      cup **stays OUT**. If it stays OUT, record it under `## Comments` and send it back to
      the planner; do not add a shutdown handler or any software path to make it pass
- [ ] **T7 USB unplugged with the cup OUT** returns the cup IN, confirmed by observation.
      Record how the T7 is powered (USB alone, or an external supply). On replug the
      relays must not click and the cup must stay IN. If they click, read the T7's
      power-up defaults for FIO0 and FIO1 in Kipling and record them
- [ ] Findings recorded under this ticket's `## Comments`, including any measured value
      that differs from what the spec assumed

## Comments

### 2026-11-08 bench (developer)

**LJTRD and relay board**
- LJTRD resistor test done per the datasheet: the switch closes only when FIO0 is
  output-high (resistor values not recorded).
- 5 V coil supply is a WR9HA1500C9P-Y on Dupont pigtails, polarity verified. `JD-VCC`
  jumper removed.
- With `JD-VCC` off, CB37 `Vs` (pin 27) must go to `VCC` on the relay board's input
  header. It powers the optocoupler inputs, and the relays do not switch without it.
- Both GND pins on the relay board are on the same net.
- Relays switch from FIO0 and FIO1 in Kipling.

**Controller: P1 controls cup #3 only (differs from what the spec assumed)**
- The end-station cup's DB9 was on another controller channel. P1 only commands and
  reports cup #3, so the cup was moved to channel 3. Now in the spec ("The hardware
  chain") and the `cup_config.py` docstring.
- **Still to do:** update the controller's front-panel labels for the channel move.

**P1 measurements (AUTO, open circuit)**
- 1-9 = 28 V DC, 5-13 = 0 V. Jumping 5-13 alone does not change 1-9.
- Jumpers on the D15 breakout: 1-9 alone, no motion; 5-13 alone, no motion; both, cup
  OUT. Two-closure fail-into-the-beam logic confirmed.
- Status pairs 3-11, 2-10 and 4-12 read 0 V across each (dry, safe for the T7). 4-12 is
  closed in AUTO; 3-11 (IN) and 2-10 (OUT) follow the cup. With power off, 10, 11 and
  12 are not connected to each other.

**Cabling as built (controller in the control room, about 75 ft)**
Two 4-conductor shielded cables, each two twisted pairs with a drain per pair. Each
cable's shield is grounded at one end only, on a different ground, so neither cable
joins the controller's ground to the LabJack's.

| Cable | Controller end (P1 breakout) | LabJack end | Drains |
|---|---|---|---|
| A, command | pair 5/13; pair 1/9 | 5/13 to relay 1 COM/NO; 1/9 to relay 2 COM/NO (never NC) | P1 pin 8 at the controller only; cut back and insulated at the relay end |
| B, status | 3 (IN), 2 (OUT), 4 (AUTO); 10, 11, 12 jumpered to the return conductor | 3 to CB37 5 (FIO2); 2 to CB37 23 (FIO3); 4 to CB37 4 (FIO4); return to CB37 1 (GND) | CB37 GND at the LabJack end only; cut back and insulated at the controller |

- Nothing else on pin 8. The 10/11/12 jumper does not touch pin 8.
- FIO2-FIO4 set to input before the status lines were landed.

**Result**
- Everything connected as above. Commanded insertion and retraction from the RBL app
  work, and the status bits read correctly. A closed contact reads 0, matching the
  decode function's inversion.
- Pin mapping in code checked against the as-built wiring: `cup_config.py` (FIO0
  enable, FIO1 command OUT, status bits 2/3/4 = IN/OUT/AUTO) matches, and
  `cup_status.py`, `cup_actuation_link.py` and `labjack_driver.py` use only those
  constants. The one hard-coded value is `FIO_DIRECTION` `0xE003` in
  `labjack_driver.py` (FIO0-1 outputs, FIO2-4 inputs). It is correct, but it must
  change if the lines ever move.

**Still open on this ticket (next bench session)**
- Hand-move check of all four states, including mid-travel (IN and OUT both reading 1)
  and AUTO vs. not-AUTO, recorded explicitly.
- Fail-safes: both relays de-energised leaves the cup IN; coil power pulled while OUT
  returns it IN.
- End task on RBL while OUT: record whether the cup stays OUT (expected) or returns IN.
- T7 USB pulled while OUT: cup returns IN, how the T7 is powered, and no relay click on
  replug.
- 10-minute idle flicker check: no `position_transition` or `fault_impossible_status`
  rows in the session file.
- Record the wire colours, and the readings of 10/11/12 to pin 8 and to chassis with the
  controller off.
