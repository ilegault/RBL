# ADR 0009 - Slits are homed on the home switch's release edge, not with HM

- **Status:** Accepted
- **Date:** 2026-11-18

## Context

Each slit drive's optical switch box (NEC 2HA062601, part of the BDS18 slit drive
assembly) feeds the Galil's home input and its reverse limit input, and the two trip at
the same point: jaw fully in, 0.2 mm from beam centre. The box is a packaged unit and is
not to be opened. Measured on 2026-11-18: after a failed edge-return pass, axis B at
rest read reverse limit and home switch both tripped (`R● H●`), and BL/FL read
-2,147,483,648 / 2,147,483,647 on all four axes, so no soft limit is involved.

The Galil's `HM` on a stepper is two stages: search toward the home input's transition,
then reverse and re-approach it at `HV`, ending with stop code 10. On these slits the
transition stage 1 is looking for is also the reverse limit tripping. A limit trip
stops motion in that direction (decelerating at `SD`) and ends `HM` with stop code 3;
stage 2 never runs. So no home pass can end on stop code 10, at any speed.

Since ticket 123 (2026-11-17) a pass that does not end on stop code 10 is refused, so
every Home, Seek + Home, Auto-Home and edge-return run fails with "did not stop on the
home switch (stop code 3)". Before ticket 123 the same thing happened silently: zero was
defined wherever the limit happened to stop the axis, which is deeper past the switch
the faster the axis arrived.

## Decision

1. **Homing does not use `HM` or `FE`.** A home pass is two parts:
   - **Approach**: drive in (negative counts) until the axis stops with the home switch
     tripped. On these slits the reverse limit is what stops it; that is expected, not
     a fault. Stopping with the reverse limit tripped and the home switch clear, or
     with neither, fails the pass.
   - **Creep out**: move +1 count, wait until the axis is at rest, read the home switch;
     repeat until it reads clear. The position where it first reads clear is the
     pass's **edge**.
2. Moving outward is allowed while the reverse limit is active, so the limit never
   interrupts the creep. The edge is always approached from the same side (from inside,
   moving out) and always read at rest, so approach speed does not move it.
3. **Homing is two home passes that must agree.** Pass 1 starts with the approach (at
   the seek speed after a seek, otherwise at the approach speed). Pass 2 backs out 300
   counts and approaches at the approach speed. If the two edges differ by more than
   4 counts (half of an 8-count lost-step jump) homing fails and no zero is defined.
   Otherwise zero (`DP 0`) is defined at pass 2's edge.
4. If the home switch has not released after 400 counts of creep, the pass fails with a
   message that says to check the sensor.
5. The edge-return test measures this same home pass.
6. Homing runs one axis at a time. The simultaneous "Auto-Home All — Together" path is
   retired.

## Consequences

- Zero sits at the release point, a few counts outside the trip point (the switch's
  hysteresis, which the switch map measures). `MM_ZERO_OFFSET` (0.2 mm) is unchanged; it
  was approximate before too.
- The three home pass speeds go away. One approach speed remains (default 58 cps).
- Homing is slower: each creep step is a separate move and read, up to a few hundred
  per pass.
- Stop code 10 no longer gates anything in homing.
- If the slits are ever rewired with a separate reverse limit beyond the home switch,
  `HM` would work again. The release-edge pass still works on such hardware, so this
  decision does not need to change for it.

## Alternatives considered

- **Home on the limit stop** (accept stop code 3 when the home switch is also tripped).
  Zero would depend on approach speed and the limit deceleration, the same problem the
  three pass speeds were trying to tune.
- **Rewire the switch box** so the reverse limit trips past home. Needs the packaged
  box opened.
- **`FE` with CN's home direction flipped.** Changes a controller setting that `HM` and
  `FE` share, and `FE` searched the wrong way when tried in May 2026.
