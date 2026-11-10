# 14: Bench — end-to-end bring-up, and the mechanical lag from the record

**What to build:** Nothing. This is the first commanded move on a live beamline, and the
first reading of the lag the archive has been measuring for free.

**Blocked by:** 11, 12

**Status:** ready-for-developer

**An agent must not claim this ticket.**

## The bring-up is destructive if it is wrong

This drives a real cup into a real beamline. Ticket 12 must be complete and its findings
read before this starts. Do not begin with the cycle armed.

Sequence: one commanded insert and retract with no beam; then one commanded insert and
retract with beam; then a short cycle at a reduced period with somebody watching; then a
full-length cycle.

## The mechanical lag

Every insertion records both a commanded and a confirmed timestamp (ticket 11). Their
difference is the lag from relay closure through the controller and the solenoid to the
cup actually moving — the reason confirmed rather than commanded position is the run
boundary. It has never been measured; the archive measures it continuously.

Read it out of a completed session file, as a distribution rather than a single number,
and check it against the `2.0` s move confirmation timeout. If the distribution's tail
approaches the timeout, the timeout is too tight and an unattended cycle will disarm
itself on a healthy move.

### No beam (can run as soon as ticket 12 is done)

The lag is mechanical, relay to controller to cylinder, so it does not need beam.

- [ ] One commanded insert and retract with no beam, observed
- [ ] A short attended cycle with no beam: period 30 s, dwell 3 s, for 20 minutes
      (about 40 insertions), `FULL` profile. Arming needs a coefficient, charge state and
      area: enter placeholder values and write `NO BEAM - placeholder dose inputs` in the
      session notes. Every insertion will write a position-vs-current disagreement row,
      because the cup confirms IN with no current. That is expected here and is not a
      finding
- [ ] Commanded-to-confirmed lag read from that no-beam session file (min, median, max)
      and compared against the `2.0` s timeout. Record it under `## Comments` as the
      no-beam lag

### With beam

- [ ] One commanded insert and retract with beam, observed
- [ ] A short cycle at a reduced period, attended throughout
- [ ] A full-length cycle producing a complete session file
- [ ] Commanded-to-confirmed lag read out of that file as a distribution — minimum,
      median, maximum — not a single figure
- [ ] The distribution compared against the `2.0` s move confirmation timeout, with a
      recommendation if the tail comes near it
- [ ] The position-versus-current disagreement rows in that file reviewed: they are the
      first real evidence about whether `cup_config.py`'s inference thresholds are right,
      and the reason ADR 0003 decision 4 retained the cross-check
- [ ] The recorded dpa recomputed by hand from the file's own columns and confirmed
- [ ] Findings recorded under this ticket's `## Comments`
