# 114: FIO0 is the interlock line (rename from "enable")

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 113

**Spec:** `.scratch/cup-actuation/spec.md` ("The hardware chain")
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `docs/adr/0003-commanded-and-confirmed-cup-position.md`
**Glossary:** `CONTEXT.md` — **Interlock** (new). The glossary says the name "enable" is retired.

## What to build

A pure rename with **no behaviour change**. FIO0 drives relay 1 across the controller's
P1 5–13. The controller manual calls that loop the **interlock**, and the developer
confirmed on hardware that while it is open the cup cannot go OUT by any route, including
the front-panel buttons in LOCAL. The code calls it the "enable" line. After this ticket
the code, its comments and the spec use the glossary's word, so the next reader of
`cup_actuation_link.py` knows that dropping FIO0 also locks out the operator's front panel.

Blocked by 113 only because both tickets edit `src/rbl/config/cup_config.py`,
`tests/test_cup_status.py` and `tests/test_cup_actuation.py`.

## Acceptance criteria

- [ ] In `src/rbl/config/cup_config.py`, `CUP_ENABLE_LINE` becomes
      `CUP_INTERLOCK_LINE: str = "FIO0"`, with the trailing comment
      `# Relay 1 -> ESM P1 5-13 (interlock: closed is required for cup OUT, remote or front panel)`.
      The alias `CUP_ENABLE_OUT_LINE` is deleted, not renamed. Neither old name survives
      as an alias. Test: tests/test_cup_status.py::`test_line_names_and_bit_positions`,
      rewritten in place under the same name: it asserts `CUP_INTERLOCK_LINE == "FIO0"`,
      keeps its other four assertions, and asserts
      `not hasattr(rbl.config.cup_config, "CUP_ENABLE_LINE")` and
      `not hasattr(rbl.config.cup_config, "CUP_ENABLE_OUT_LINE")`.
- [ ] Every importer uses the new name: `src/rbl/hardware/labjack_driver.py` (the import
      and the `eWriteName` call in the connect/shutdown path) and
      `src/rbl/state/cup_actuation_link.py` (the import, `command_cup`,
      `_release_cup_drive`, `_on_labjack_connect_cup`). The relay drive sequence is
      unchanged. Tests: tests/test_cup_actuation.py::`test_relays_on_connect_and_disconnect`
      and tests/test_cup_actuation.py::`test_shutdown_leaves_both_outputs_deasserted_no_positional_command`,
      rewritten in place with only the import and the name substituted, still assert
      `(CUP_INTERLOCK_LINE, 1)` written on connect and `(CUP_INTERLOCK_LINE, 0)` on
      disconnect and shutdown. Every other test in `tests/test_cup_actuation.py` that
      references the old name gets the same one-word substitution and nothing else.
      Check by hand: `grep -rn "CUP_ENABLE" src tests scripts` prints nothing.
- [ ] The prose follows. Replace "enable" with "interlock" where it names relay 1 or
      FIO0, and nowhere else, in: the `src/rbl/config/cup_config.py` module docstring
      ("Enable line (FIO0)" and "both closures (enable and command)"); the
      `src/rbl/hardware/labjack_driver.py` module docstring and the `# FIO0 (enable)`
      comment; the `src/rbl/state/cup_actuation_link.py` module docstring, the
      `command_cup` docstring and the `# Energise Relay 1 (enable)` comment; the
      `tests/test_cup_actuation.py` module docstring; and `.scratch/cup-actuation/spec.md`
      line 62 ("enables cup-out control"), the FIO0 row of the hardware-chain table, and
      "Relay 1 (enable)" at line 226. The `cup_config.py` docstring entry for FIO0 also
      gains one sentence: the controller lets the cup go OUT by any route (remote command
      in AUTO, or the front-panel buttons in LOCAL) only while this loop is closed (seen
      on hardware, 2026-11-10). Check by hand:
      `grep -rn -i "enable line\|relay 1 enable\|relay 1 (enable)\|FIO0 (enable)\|enabling cup OUT\|enables cup-out" src tests .scratch/cup-actuation/spec.md`
      prints nothing. (by hand)
- [ ] The full suite passes with no test edited beyond the substitutions above. That is
      the proof that this is a rename and not a behaviour change. (no test: the gate's
      full pytest run is the proof)

**What tests may fake:** the LabJack (`ljm`) and the stream worker, as
`tests/test_cup_actuation.py` already fakes them.

**Out of scope:** `docs/adr/0003-commanded-and-confirmed-cup-position.md` and `CONTEXT.md`.
Both are held paths, and the glossary is already updated. The `lbl_auto_mode` text
"AUTO mode (remote actuation enabled)" in `src/rbl/gui/faraday_cup_tab.py`, which is
about AUTO and not FIO0. Status polarity (ticket 113). Any change to when FIO0 is driven
high or low.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
