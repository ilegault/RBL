# ADR 0010 - Slits are homed with HM, as NEC specifies

- **Status:** Accepted
- **Date:** 2026-11-23
- **Supersedes:** ADR 0009 (slits homed on the release edge)

## Context

ADR 0009 stopped using the Galil's `HM` because, on these slits, the home switch and the
reverse limit trip at the same count. `HM`'s first stage is ended by the reverse limit
(stop code 3) and its slow second stage never runs, so a check that demanded stop code 10
failed every home. 0009 replaced `HM` with a release-edge pass: drive in, then step out one
count at a time until the home switch releases, and zero there.

Bench data from 2026-11-19/20 (switch maps and edge-return runs, all four axes):

- Every switch map shows the home switch and the reverse limit tripping on the same count,
  with one clean trip and one clean release. Hysteresis: A about 9 counts, B 12-18, C about
  105, D 1. The switches are not the problem.
- The edge drifts steadily in one direction per cycle, which is lost steps, not switch
  jitter. Axis A (damaged in the jaw collision) drifts +167 counts per cycle at 900 cps and
  -380 at 58 cps. B drifts about -10 to -12 at 900. C is clean at 900 and 1800 (under 1.5
  counts per cycle). All axes slip badly at 50-58 cps.
- Auto-Home All stopped on A: two release-edge passes cannot agree within 4 counts with
  that much slip, so no zero was set.

The developer will operate the slits the way NEC's software does (NEC support e-mail
5-12496, 2026-05-29). NEC's example homes with `SPA=900`, `HMA`, `BGA`, `DPA=0`, `DEA=0`,
`SPA=1800`, after a pre-home move off the switch (`PRA=2000`, `BGA`), and never checks the
stop code. On these slits that zeroes wherever the limit stops the axis at 900 cps. At a
fixed speed and fixed limit deceleration that point is repeatable. The developer wants the
edge-return test to measure exactly what `HM` does, so the test sends `HM` too.

The developer also suspects the one-count steps of the release-edge pass may lose steps.
The edge-return test keeps the release edge for that reason: every cycle records both
where `HM` stopped and where the switch released, so the two can be compared on the same
run.

## Decision

1. **Homing uses `HM`.** A home pass is:
   - **Pre-home move**, only if the axis is on the home switch or the reverse limit: one
     `PR 2000`, `BG` at the slit speed (1800 cps), wait until it stops, then both switches
     must read clear, or homing fails and no zero is set. On the forward limit no move is
     made.
   - **Home move:** `SP 900`, `HM`, `BG`, then wait until the axis is at rest.
   - **Stop check:** the pass is accepted only if the home switch reads tripped and the stop
     code is 3 (stopped by a limit) or 10 (normal end of `HM`). The reverse limit tripped
     with the home switch clear, or neither switch, fails the pass. The commanded position
     at rest is the pass's **stop position**.
2. **Homing is two home passes whose stop positions agree** within 4 counts
   (`HOME_STOP_AGREEMENT_COUNTS`). Pass 2 starts with the same pre-home move. If they agree,
   zero is defined at pass 2's stop: `DP 0` and `DE 0`, then `SP 1800`. Otherwise homing
   fails and no zero is set.
3. **The homing speed is fixed at 900 cps** (NEC's homing velocity). The operator cannot
   change it. The Approach speed box only sets the edge-return test's speed.
4. **One Home button per axis.** "Seek + Home" is retired: `HM` drives in from anywhere on
   the travel, which is what the seek did.
5. **Auto-Home All** runs one axis at a time and stops at the first failed axis, leaving the
   rest untouched, because a broken switch on one jaw could let the opposite jaw be driven
   into it. Its failure dialog names each failed axis and why it failed.
6. **The edge-return test sends the real `HM`.** Each cycle: move out by the out-counts
   (`PR`, default 2000) at the approach speed, then `SP` (approach speed), `HM`, `BG`, the same
   stop check, record the stop position, step out one count at a time to the release edge,
   record the edge. It never defines zero. The CSV holds both columns.
7. The release-edge pass survives only inside the edge-return test. The switch map is
   unchanged.

## Consequences

- Zero sits at the trip point plus the limit deceleration at 900 cps, a few counts inside
  where 0009 put it (0009 used the release point). `MM_ZERO_OFFSET` (0.2 mm) and the
  629.92 counts/mm scale are unchanged.
- Homing is faster: no hundreds of one-count steps.
- The stop-code-10 gate from ticket 123 stays retired. Stop code 3 is now an expected home.
- The two-pass agreement is the app's only built-in lost-step alarm. Whether 4 counts is
  the right limit is decided from the edge-return stop column (bench ticket).
- If the slits turn out to have encoders wired to the Galil (`MG _TPx` changes with
  motion; NEC's e-mail says both that there are none and that `TP` reads the encoder),
  stepper position maintenance (`YS`/`QS`) becomes possible. That is a separate effort.

## Alternatives considered

- **Keep the release-edge pass (ADR 0009).** Its zero does not depend on speed, but it is
  not what NEC's software does, and it is the suspect the developer wants to rule out. It is
  kept as a measurement inside the edge-return test, where it can be compared with `HM`.
- **One `HM` pass, as NEC does.** It has no way to notice lost steps. Two passes cost a
  few seconds.
- **`FE`** (named in NEC's run-down). `FE` was seen searching the wrong way on this setup in
  May 2026. `HM`'s first stage was seen driving in correctly on 2026-11-18.
