# 52: Bench campaign: measure all four plates, swap the amplifiers, run 12 hours

**Status:** ready-for-developer

**Runner:** developer

**Auto-merge:** no

**Blocked by:** 39, 42, 45, 50, 51, 60

**Spec:** `.scratch/amplifier-load-and-spikes/spec.md` (Further Notes)
**Binding:** `docs/adr/0005-drift-pass-on-plates-guarded-by-protections-not-a-clock.md`, `docs/adr/0006-amplifier-faults-are-observed-not-stopped-during-a-session.md`

**Read first:** The spec's Further Notes.

## What to build

Done by the operator at the beamline, not by an agent.

The HV path can be broken only at the **amplifier** end: the cables unplug from the
amplifier outputs, and the plate end stays connected. So two load conditions are
measurable on this beamline, **on plates** and **disconnected**. **Cable only** is not
(it needs the plate end open) and is not run.

1. Record the initial amplifier assignment (serials) on the Amplifiers panel.
2. For each plate, on plates: the Mode C ladder and Mode A.
3. A clamp test on each amplifier, on plates.
4. Unplug the HV cables at the amplifier outputs. With nothing on its output, run the
   Mode C ladder and Mode A on each amplifier, load condition **disconnected**, selected
   at its current plate position so the result carries the right serial. No clamp test
   disconnected: with no load there is no current to clamp. Cap or guard each open
   output before raising HV.
   **Resistor check of the current-monitor scale (ADR 0007), one amplifier, while its
   cable is off.** Put a 10 kΩ resistor rated 5 W or more from that amplifier's output
   to ground through a mating HV-connector pigtail. Drive DC from its function
   generator until the output reads 100 V on a meter across the resistor, and never
   above 100 V with the resistor fitted. The current is that voltage / 10 kΩ (10 mA at
   100 V). Read the raw current-monitor voltage: **~5 V** confirms 1 V = 2 mA, **~1 V**
   means the manual was right. Note the voltage monitor's reading against the meter in
   the same run. If no mating pigtail is on hand, skip this check and say so in
   Comments; it is not a gate for the rest of the campaign.
5. Reconnect with X and Y swapped at the amplifier end, record the swap on the
   Amplifiers panel, then repeat steps 2 and 3 on plates.
6. A 12 h drift pass at the experiment's exact triangle, frequency and amplitude.
7. Fill the presentation's capacitance table and replace its illustrated 1.6 nF.

## Acceptance criteria

- [ ] The Load Characterization view shows an on-plates and a disconnected result for every plate position;
  cable only reads `not measured` and Comments say why.
- [ ] A clamp test result exists for each of the four amplifiers.
- [ ] The resistor check's meter voltage, raw current-monitor voltage and amplifier serial are under `## Comments`, or Comments say why it was skipped.
- [ ] A swap is recorded, and on-plates results exist for both assignments.
- [ ] The 12 h drift pass folder contains `spikes.csv`, and the run completed or stated why it ended.
- [ ] Findings are written under `## Comments` here, including whether a problem followed the amplifier or the plate.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments

2026-10-07 (planner): Now blocked by current-monitor-scale ticket 60. Every capacitance and current measured before ADR 0007 used the wrong scale (1 V = 10 mA; it is 1 V = 2 mA), so this campaign runs only after the old data is archived and the dial-50 clamp check has been done. Step 6's illustrated 1.6 nF is superseded: expect roughly 300 pF on plates.

2026-11-08 (planner): The operator confirmed the HV cables can be unplugged at the
amplifier outputs (that is how the X/Y swap is done) but not at the plate end. Steps
rewritten: on plates and disconnected only; cable only dropped as not measurable here.
The capacitance split is therefore amplifier alone vs. cable + feedthrough + plates
together, not three ways.
