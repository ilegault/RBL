# 44: The Mode C ladder stops on any of four rules, and the interlock gates each rung

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 43

**Spec:** `.scratch/amplifier-load-and-spikes/spec.md` (Part B)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `docs/adr/0006-amplifier-faults-are-observed-not-stopped-during-a-session.md` (decision 2: characterization runs keep their aborts)

**Read first:** `src/rbl/services/load_characterizer.py` (`_on_collect_complete` - the Mode B leakage abort is the pattern to copy), `src/rbl/services/calibration_runner.py` (`_check_overcurrent` - reuse `CAL_TRIP_HARD_MA` and `ma_unclamped`), `src/rbl/hardware/hv_interlock.py` (`interlock_status`).

## What to build

The ladder ends (no further rung commanded, outputs zeroed through `_finish`) when any of these
holds, in this order:

1. `hard_trip`: any current sample on the driven channel above `CAL_TRIP_HARD_MA`, converted
   with `ma_unclamped` so a railed monitor counts (checked on every window, not only at rung end);
2. `c_changed`: a rung's `c_pf_mean` differs from the first rung's by more than 10 %;
3. `leakage`: `inter_edge_leak_ua` above `MODE_B_LEAK_THRESHOLD_UA_DEFAULT`;
4. `not_following`: `measured_swing_kv` below 90 % of the commanded swing (2 x rung_kv).

Before each rung, `interlock_status(pressure, rung_kv)` must permit it, with pressure from the
characterizer's existing `pressure_provider`; otherwise the ladder ends with `interlock`. An
ended ladder writes a result with `aborted: true`, `abort_rule`, `abort_rung_kv` and the
completed rungs in `points`. `finished` still emits.

Tests may fake: the function generator, stream windows and `pressure_provider`.

## Acceptance criteria

- [x] Rung 3 synthesised at 1.25x the first rung's C: the ladder stops after rung 3 with `abort_rule: 'c_changed'`, `abort_rung_kv: 2.0`, three entries in `points`, and the fake generator never receives a 3 kV command.
- [x] Rung 2 with a 100 uA offset between edges stops with `leakage`; rung 2 with a swing of 85 % of commanded stops with `not_following`.
- [x] One sample above `CAL_TRIP_HARD_MA` during rung 1 stops with `hard_trip` before rung 1's point is emitted.
- [x] A `pressure_provider` whose pressure the interlock does not permit at 3 kV stops before rung 4 with `interlock`, and 3 kV is never commanded.
- [x] A clean ladder completes six rungs and its result has no `abort_rule`.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments

2026-10-07: Done. The Mode C ladder ends on `hard_trip` (per window, SETTLE included, via `ma_unclamped`), `c_changed` (>10 % from rung 1), `leakage` (> 50 uA) or `not_following` (swing < 90 % of 2 x rung_kv, or no edges at all), and the HV interlock gates every rung; an ended ladder writes `aborted`, `abort_rule`, `abort_rung_kv` and its completed rungs, and `finished` still emits. The tab status names the rule. `LoadCharacterizer.abort_rule` is the new read-only property. Tests: `TestModeCLadderAborts` (one per criterion plus boundaries and ordering).
Decisions and consequences to know about:
- Unknown pressure (the default provider returns NaN, as does an absent gauge) BLOCKS the first rung. Existing and new Mode C tests now pass `pressure_provider=good_vacuum`.
- A rung with no edges at all counts as `not_following`.
- The existing Mode C synthetic data (`test_finds_edges_and_recovers_capacitance_ballpark`, and the 37 test) used 20 us edges, i.e. a 120 mA peak, which this ticket's own hard-trip rule defines as a short. The input could not stay and the test still mean "trip-free ladder", so those edges are now 300 us (peak 8 mA): the assertions are unchanged. This is a harness change forced by the new rule, not a weakened assertion.
- BENCH CONCERN for ticket 52: charging ~1.6 nF with a square edge from an amplifier that limits at ~100 mA means every real rung's edge current may exceed `CAL_TRIP_HARD_MA` (60 mA), so `hard_trip` could end every real ladder at rung 1. The rule is implemented exactly as written; if the bench shows that, the decision for a human is whether to judge the hard trip outside the edge windows or on a different level.
