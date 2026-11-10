# 60: Bench: archive the old data on the beamline PC, then a clamp test at pot dial 50

**Status:** ready-for-developer

**Runner:** developer

**Auto-merge:** no

**Blocked by:** 56, 57, 58, 59

**Spec:** `.scratch/current-monitor-scale/spec.md` (Further Notes)
**Binding:** `docs/adr/0007-current-monitor-scale-is-2-ma-per-volt.md`

**Read first:** `docs/hardware/eel5000-manufacturer-notes.md` sections 4 and 6.

## What to build

Done by the operator at the beamline, not by an agent.

1. Build and install the app with tickets 53-59 merged.
2. On the beamline PC, run `python scripts/archive_pre_adr_0007.py` (a dry run). Read
   every `would move` line and check that nothing unexpected is listed.
3. Run it again with `--apply`, and confirm both `README.txt` files exist.
4. Start the app, and check that the Load Characterization view shows `not measured` for
   every plate and condition.
5. Set one amplifier's CURRENT pot to dial 50, which the manufacturer says is 10 mA. Run
   the clamp test on that plate (the tab runs it at 1 kV peak; there is no setting).
   Read the outcome against the two hypotheses. A triangle of peak V at frequency f
   into a capacitance C draws a square-wave current of height 4·C·V·f:
   - **Manufacturer right (1 V = 2 mA, plates ~330 pF).** 10 mA needs ~7.6 kHz at
     1 kV, above the ladder's 5000 Hz top step. Expect `not reached`, with the raw
     current monitor's flat top near **3.3 V** at 5000 Hz. This is the expected result
     and it confirms ADR 0007.
   - **Manual right (1 V = 10 mA, plates ~1.6 nF).** 10 mA is reached by ~1.6 kHz.
     Expect a clamp at the 1500 or 2000 Hz step with the raw monitor flat near
     **1 V**. Stop: ADR 0007 must be revisited before anything else runs.
   - **Anything else** (a clamp at a raw level well below the pot setting, or at a
     frequency neither case predicts) is a bandwidth or slew limit, not a scale
     verdict. Stop and record it.
6. Set the pot to dial 20 (about 4.4 mA, if the pot is linear across 0.5-20 mA; the
   manufacturer has not said it is) and run the clamp test on the same plate again.
   This run is the one that shows a flat top:
   - **1 V = 2 mA:** clamps at about 4000 Hz (3000 Hz if C is larger than ~330 pF),
     raw monitor flat near **2.2 V**.
   - **1 V = 10 mA:** clamps by about 750 Hz, raw monitor flat near **0.44 V**.
   The 5x gap between the two is the measurement; pot nonlinearity cannot close it.
7. The tab's clamp current is the current's **fundamental**, and the fundamental of a
   square wave is 4/pi (1.27x) its height. A flat top at 2.2 V (4.4 mA) shows in the
   tab as about 5.6 mA. Record the raw monitor voltage as the verdict, and the tab's
   number beside it.
8. Return the pot to fully clockwise (dial 100).

## Acceptance criteria

- [ ] The dry run listed only the stores in ticket 59, and `--apply` printed one `moved`
  line per store (by hand).
- [ ] The app starts after archiving, and every plate and condition reads `not measured`
  (by hand).
- [ ] The dial-50 clamp test result (`not reached`, or the clamp frequency and current) is
  written under `## Comments` with the raw monitor voltage at the top step, plate and
  date (by hand).
- [ ] The dial-20 clamp test result is written under `## Comments` with the clamp
  frequency, the tab's clamp current and the raw monitor flat-top voltage (by hand).
- [ ] Comments state which hypothesis the two runs support (by hand).
- [ ] The pot is back at dial 100, noted under `## Comments` (by hand).

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
