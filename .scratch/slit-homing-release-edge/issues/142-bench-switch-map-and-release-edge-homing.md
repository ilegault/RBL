# 142: Bench — switch map, release-edge homing and edge-return on all four slits

**Status:** ready-for-developer

**Runner:** developer

**Auto-merge:** no

**Blocked by:** 135, 136, 137, 138, 139, 140, 141

**Spec:** `.scratch/slit-homing-release-edge/spec.md`
**Binding:** `docs/adr/0009-slits-homed-on-the-release-edge.md`

## What to build

Nothing to code. Prove on the beamline that homing works with the shared home/reverse switch, and
record the numbers the follow-ups need. Replaces the edge-return and pass-speed items of ticket 134.

## Acceptance criteria

- [ ] (by hand) **Switch map**, Stepper Motors tab, each axis A–D one at a time, slits clear of the
      beam: paste the four console summary lines below and name the four CSVs. Note for each axis
      whether the home switch and the reverse limit trip at the same count, and the trip-to-release
      gap (hysteresis) in counts.
- [ ] (by hand) **Seek + Home** on each axis: paste the success line (`edges …, … cts, DP=0 at pass 2`)
      or the failure message.
- [ ] (by hand) **Auto-Home All — One at a Time**: one full run; paste its summary line.
- [ ] (by hand) **Edge-return test**, each axis, 10 cycles, default out counts, default approach
      speed: paste the four summary lines and name the CSVs. Note any lost-step jumps.
- [ ] (by hand) **Follow-ups**: write down whether the 0.2 mm zero offset (`MM_ZERO_OFFSET`) needs
      changing now that zero is at the release point, and whether the 4-count agreement limit
      should change given the edge-return spread. Each "yes" becomes a new ticket.

## Comments
