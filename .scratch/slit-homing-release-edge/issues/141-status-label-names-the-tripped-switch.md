# 141: The axis status says exactly which switch is tripped

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/slit-homing-release-edge/spec.md`
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `CONTEXT.md` (Slit motion: *home switch*, *reverse limit*),
`src/rbl/gui/motor_tab.py` (`AxisControls.update_state`, the `"REV/HOME LIMIT active"` branch and
the `"FWD LIMIT active"` branch above it), `tests/test_auto_home.py` (the `tab` fixture, which
builds a real `MotorTab` on a real `Beamline`).

## What to build

The home switch and the reverse limit are different things (glossary), and on these slits they
trip together. Each axis panel's status shows which one is tripped instead of lumping them as
"REV/HOME LIMIT active".

In `AxisControls.update_state`, keep the moving, disabled and forward-limit branches as they are.
Replace the `sw["reverse_switch"] or sw["home_switch"]` branch with: both tripped →
`"HOME + REV LIMIT"`; only the reverse limit → `"REV LIMIT"`; only the home switch → `"HOME"`;
all with the existing orange bold style.

## Acceptance criteria

- [ ] Test `test_status_names_both_switches_when_both_trip` in
      `tests/test_motor_tab_switch_status.py` (new file): a real `MotorTab(Beamline())`; calling
      `tab.axes["B"].update_state({"pos": -6537, "moving": False, "enabled": True, "switches": {"forward_switch": False, "reverse_switch": True, "home_switch": True}})`
      leaves `lbl_status.text() == "HOME + REV LIMIT"`.
- [ ] Test `test_status_names_the_reverse_limit_alone` in the same file: reverse only →
      `"REV LIMIT"`.
- [ ] Test `test_status_names_the_home_switch_alone` in the same file: home only → `"HOME"`; and
      with all three clear the text is `"Idle  [enabled]"` as before.

**What tests may fake:** nothing beyond the offscreen Qt platform; `MotorTab`, `AxisControls` and
`Beamline` are real.

**Out of scope:** the `F:/R:/H:` dots line (unchanged). Homing (135–138). The Overview.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
