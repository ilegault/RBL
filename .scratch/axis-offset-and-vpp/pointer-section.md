## Set 10 (new): axis offset on the Overview, amplitude in Vpp everywhere, Send to drive

- **Spec:** `.scratch/axis-offset-and-vpp/spec.md`
- **Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`. The ±5 V interlock is
  load-bearing (AGENTS.md §7).
- **Glossary (new in `CONTEXT.md`, committed with the spec, no ticket):** a **Raster drive**
  section with **Amplitude**, **Offset**, **Linked pair** / **unlinked pair** and **Input peak**,
  plus a Known collisions entry: "Peak" means three things.
- **Tickets:** `.scratch/axis-offset-and-vpp/issues/106...112`. 112 is `ready-for-developer`
  (bench): an agent must not claim it.
- **Tracker conventions:** `docs/agents/issue-tracker.md`

### Next

- **106**, **107** and **109** are unblocked and touch different files, so they can run in
  parallel. Start with **109** (the setpoint model): 110 and 111 both wait on it.
- **108** after 106 (both edit `funcgen_tab.py`). **110** after 107 and 109. **111** after 109.
- Then **112** (developer, bench).

### Dependency order

```
106 ── 108 ──────────────┐
107 ─┐                   │
109 ─┴─ 110 ─────────────┼─ 112 (developer, bench)
109 ─── 111 ─────────────┘
```

### Requirements that are not preferences

- `Beamline.set_channel` and `Beamline.apply_all_channels` (`src/rbl/state/funcgen_control.py`)
  are not edited. Greying Apply is a front-end courtesy; Beamline stays the backstop.
- The link flag lives in `FuncGenSetpoints` (non-GUI) so it is testable without widgets,
  and is never saved. `update_axis` drops `offset_v`.
- Every greying decision calls `over_limit_channels` in `funcgen_safety.py`. No widget
  repeats the 5 V comparison.
- Send to drive never sends a hardware command and never changes `output_on`.
- Planner text and the Send-to-drive enable rule are pure functions, tested with plain dicts.
- The peak-volt test in `tests/test_overview_tab.py` is rewritten in place under its own
  name; no test is deleted.

### Not in this set

Saving setpoints or link state; per-plate amplitude or frequency on the Overview; the warn
threshold; the HV pressure interlock; the Overview amplifier bars; the planner's kV physics
lines; applying to hardware from the planner.

---
