# 142: Bench — switch map, release-edge homing and edge-return on all four slits

**Status:** ready-for-developer

**Runner:** developer

**Auto-merge:** no

**Blocked by:** 135, 136, 137, 138, 139, 140, 141, 144, 147, 148, 149, 150, 151

**Spec:** `.scratch/slit-homing-release-edge/spec.md`
**Binding:** `docs/adr/0009-slits-homed-on-the-release-edge.md`

## What to build

Nothing to code. Prove on the beamline that homing works with the shared home/reverse switch, and
record the numbers the follow-ups need. Replaces the edge-return and pass-speed items of ticket 134.

## Acceptance criteria

- [ ] (by hand) **Controller settings before the new build.** Before running a build that contains
      147–151 (use the previous build's terminal, or any Galil terminal), paste the replies to
      `MG _KSA,_KSB,_KSC,_KSD` and `MG _LCA,_LCB,_LCC,_LCD`; send `SP 1` on axis A, paste `MG _SPA`
      (expected 0), then send `SP 1800`; and with each axis at rest after connecting, paste
      `MG _TDx,_RPx` for x = A–D (expected equal; if not, the at-rest wait from 148 will report "step
      pulses still pending" on that axis).
- [ ] (by hand) **Switch map**, Stepper Motors tab, each axis A–D one at a time, slits clear of the
      beam: paste the four console summary lines below and name the four CSVs. Note for each axis
      whether the home switch and the reverse limit trip at the same count, and the trip-to-release
      gap (hysteresis) in counts.
- [ ] (by hand) **Seek + Home** on each axis: paste the success line (`edges …, … cts, DP=0 at pass 2`)
      or the failure message.
- [ ] (by hand) **Auto-Home All — One at a Time**: one full run; paste its summary line.
- [ ] (by hand) **Edge-return test**, each axis, 10 cycles, default out counts, default approach
      speed (900 cps after 147): paste the four summary lines and name the CSVs. Note any lost-step jumps.
- [ ] (by hand) **Speed comparison on axis A**, 10 cycles each, one CSV each: approach 58 cps and
      1800 cps at the default out counts, and 900 cps with out counts 50 (most of each cycle is then the
      one-count creep). Paste the three summary lines. Compare the spreads and any 8-count jumps: 8-count
      jumps are stalls on the out or in legs; a wider spread at out counts 50 than at 2000 points at the
      one-count steps themselves.
- [ ] (by hand) **Follow-ups**: write down whether the 0.2 mm zero offset (`MM_ZERO_OFFSET`) needs
      changing now that zero is at the release point, and whether the 4-count agreement limit
      should change given the edge-return spread. Each "yes" becomes a new ticket.

## Comments

**2026-11-19 (planning):** Added 144 to Blocked by. Once 144 lands (the Approach speed goes up to 2000
cps), add this to the edge-return item: on axis A, run the edge-return test (10 cycles, default out
counts) three times, at approach speeds 58, 900 and 1800 cps, one CSV each (the CSV's
`pass_speed_cps` column labels them). Paste the three summary lines and compare the spread and any
8-count jumps. The question is whether axis A loses more steps moving in at slow speed, as seen by eye
on the bench, and whether NEC's 900 cps is the better default. A default change is a new ticket and an
ADR 0009 update. See the spec's Addendum 2026-11-19.

**2026-11-20 (planning):** Added 147–151 to Blocked by. The default speeds are now NEC's (1800 slit
speed, 900 for homing, the switch map and the approach), the edge-return test moves out at the approach
speed it measures, and switch readings wait until every step pulse is sent. The 2026-11-19 comment's
three-speed comparison is now the "Speed comparison on axis A" criterion, plus a 900 cps run at out
counts 50. The controller-settings criterion records what the controller held before the new build
sends `KS 2`. See the spec's Addendum 2026-11-20.
