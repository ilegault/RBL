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

1. Record the initial amplifier assignment (serials) on the Amplifiers panel.
2. For each plate: the Mode C ladder and Mode A, disconnected, cable only, and on plates.
3. A clamp test on each amplifier.
4. Record an amplifier swap (X and Y), then repeat steps 2 and 3 on plates.
5. A 12 h drift pass at the experiment's exact triangle, frequency and amplitude.
6. Fill the presentation's capacitance table and replace its illustrated 1.6 nF.

## Acceptance criteria

- [ ] The Load Characterization view shows a result for every plate in all three load conditions.
- [ ] A clamp test result exists for each of the four amplifiers.
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
