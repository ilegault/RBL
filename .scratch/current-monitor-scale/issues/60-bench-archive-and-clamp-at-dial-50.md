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
   the clamp test on that plate at 1 kV.
6. Record the clamp current and the raw current-monitor voltage where it flattened:
   - **~5 V (10 mA)** confirms 1 V = 2 mA.
   - **~1 V** means ADR 0007 must be revisited before anything else runs.
7. Return the pot to fully clockwise (dial 100).

## Acceptance criteria

- [ ] The dry run listed only the stores in ticket 59, and `--apply` printed one `moved`
  line per store (by hand).
- [ ] The app starts after archiving, and every plate and condition reads `not measured`
  (by hand).
- [ ] The clamp test at dial 50 reports a clamp current, written under `## Comments` with
  the raw monitor voltage, plate, frequency and date (by hand).
- [ ] The pot is back at dial 100, noted under `## Comments` (by hand).

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
