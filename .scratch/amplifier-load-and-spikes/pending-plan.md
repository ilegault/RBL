# Active work: two ticket sets: logging and sessions (unfinished), then amplifier load and spikes

This is a pointer, not the work.

## Set 2 (still open): daily vacuum monitoring log, sessions, the cup log

- Spec: `.scratch/logging-and-sessions/spec.md`
- ADRs: `docs/adr/0004-monitoring-log-and-sessions.md`; amendments dated 2026-10-05 in
  `docs/adr/0002-...md` (B1-B5) and `docs/adr/0003-...md` (C1-C5)
- Tickets: `.scratch/logging-and-sessions/issues/19...33`. 19, 20, 22, 25 are done; 21-33
  otherwise remain. Unchanged from the previous pointer.

## Set 3 (new): measured amplifier loads with history, a current-vs-frequency planner, a spike recorder

- Spec: `.scratch/amplifier-load-and-spikes/spec.md`
- ADRs (new, binding): `docs/adr/0005-drift-pass-on-plates-guarded-by-protections-not-a-clock.md`,
  `docs/adr/0006-amplifier-faults-are-observed-not-stopped-during-a-session.md`.
  Binding on all test work: `docs/adr/0001-tests-first-and-no-muted-failures.md`.
- Glossary: `CONTEXT.md`, new section "Amplifier limits and load" (continuous rating, burst
  rating, recovery period, operating point, reference current, spike threshold, current
  spike, load capacitance, load condition incl. cable only, characterization result,
  hardware change, plate position, amplifier, amplifier assignment, amplifier swap); new
  known collision "Trip" means two things.
- Tickets: `.scratch/amplifier-load-and-spikes/issues/34...52`
- Tracker conventions: `docs/agents/issue-tracker.md`

## Next

- **Set 3, ticket 34** first: it redirects the two new stores to temp paths in tests. Every
  later ticket in set 3 writes to them, and the suite once wrote a fake capacitance into the
  operator's real store.
- **Set 3, tickets 41 and 46** (pure modules) can run alongside 34.
- **Set 2, tickets 21 and 23** can run alongside all three. No two of these five touch the
  same file.

## Dependency order (set 3)

```
34 ─┬─ 35 ── 36 ─┬─ 37 ─┬─ 38 ── 39 ─┐
    │            │      └─ 43 ── 44 ─┴─ 45 ──┐
    │            ├─ 40 ──────────────────────┼──────── 51 ─┐
    │            └──────────── 42 (also 41)  │             │
    └──────────────── 47 (also 46) ─┬────────┼── 50        │
                                    ├─ 48 (also 36, set-2 33) ── 49
                                    └────────────────── 51
41 ── 42
46 ── 47
52 (ready-for-developer, bench) blocked by 39, 42, 45, 50, 51
```

- 38, 39, 45 and 50 are chained because each edits `load_characterization_tab.py`.
- 48 waits for set 2's 33 because both edit `overview_tab.py` and `session_recorder.py`.
- 52 is `ready-for-developer`: an agent must not claim it.

## Requirements that will be quietly treated as preferences

1. **No fallback capacitance anywhere.** An unmeasured plate says `not measured`; the only
   assumed value is `SIZING_ASSUMPTION_PF` (3000), used only to size a first amplitude.
2. **New modules read paths through `rbl.config.paths` at call time**, so the conftest
   fixtures from 34 cover them.
3. **History and assignment modules never read a clock**; time is an argument.
4. **During a session or drift pass, nothing turns an amplifier output off or opens a
   blocking dialog** because of a spike or regulation fault (ADR 0006). Characterization
   runs keep their aborts.
5. **The spike recorder commands nothing.**
6. **Results are never overwritten**; names go through `log_rollover.unused_path`.
7. **Spike threshold is max(2 x reference, reference + 5 x noise)**, nothing else.

## What set 3 does not do

No LIMIT/TRIP monitor wiring, no automatic stops during sessions, no stream-rate change, no
oscilloscope capture, no import of old `load_calibration.json` values, no change to the
vacuum HV interlock.
