# 109: Linked offsets in the setpoint model

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/axis-offset-and-vpp/spec.md` (decisions D7–D11)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

## What to build

The shared setpoint model `FuncGenSetpoints` (`src/rbl/state/setpoints.py`) learns the
**linked pair** (`CONTEXT.md` "Raster drive"). On a linked axis, the minus plate's offset is
always the negative of the plus plate's. The flag lives here, not in a widget, so that the
Overview, the FG tab, the Raster Planner and the tests all read one flag without building a
widget. This ticket has no UI; tickets 110 and 111 build on it.

## Acceptance criteria

- [ ] New on `FuncGenSetpoints`, with these exact names: `link_changed = Signal(str, bool)`,
      `axis_linked(axis) -> bool` (True for both axes on construction, never saved),
      `set_axis_offset(axis, offset_v)`, `set_linked(axis, linked)`,
      `send_plan(axis, amp_vpp, offset_v, freq_hz)`. Tests in
      `tests/test_setpoint_sync.py` construct `FuncGenSetpoints()` directly, with no
      widget. Test: tests/test_setpoint_sync.py::`test_axes_start_linked`.
- [ ] `set_axis_offset("X", 0.4)` leaves `get("A1").offset_v == 0.4` and
      `get("A2").offset_v == -0.4`. `set_linked("X", False)` changes no offset and emits
      `link_changed("X", False)` once; a second call emits nothing. Re-linking, with
      A1 = 0.3 and A2 = 0.1, gives A2 == −0.3 and emits `link_changed("X", True)`. Test: tests/test_setpoint_sync.py::`test_linked_offset_is_equal_and_opposite`.
- [ ] On a linked axis, `update("A1", offset_v=0.5)` while A2 is −0.4 unlinks X and emits
      `link_changed("X", False)`. `update("A2", offset_v=-0.4)` when A1 is 0.4 (still
      equal and opposite, `|plus + minus| <= 1e-9`) does not unlink. Edits to amplitude,
      frequency or output never change the link. Test: tests/test_setpoint_sync.py::`test_channel_offset_edit_unlinks_the_pair`.
- [ ] `update_axis("X", offset_v=1.0, amp_vpp=2.0)` changes amplitude only: `offset_v` is
      dropped the same way `phase_deg` already is, and the docstring says why (a
      common-mode offset moves nothing). Test: tests/test_setpoint_sync.py::`test_update_axis_refuses_to_touch_offset`.
- [ ] `send_plan("Y", 4.0, 0.25, 517.0)` on an unlinked Y, with B1 shape "Sine" and
      `output_on=True`, leaves both B1 and B2 at `amp_vpp=4.0`, `freq_hz=517.0` and
      `shape="Triangle"`, with B1 at +0.25 and B2 at −0.25. Y is linked again. `output_on`,
      `phase_deg`, `start_phase_deg` and `load` are unchanged on both. `axis_matched`
      is unchanged by this ticket and its existing tests pass. Test: tests/test_setpoint_sync.py::`test_send_plan_sets_a_linked_triangle_and_leaves_output_alone`.

Nothing may be faked: the model is plain Python plus Qt signals. Count signal emissions
with a list-appending slot, as existing tests in `tests/test_setpoint_sync.py` do.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --durations=25
```

## Comments
