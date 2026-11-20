# 149: Startup sends NEC's stepper smoothing (`KS 2`)

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/slit-homing-release-edge/spec.md` (Addendum 2026-11-20)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/hardware/galil_driver.py` (`GalilController.startup_sequence` and its `rep`
helper), `src/rbl/config/hardware_config.py` (the module docstring's list from the 2HA075520 spec
e-mail, and `MOTOR_SMOOTHING`), `src/rbl/gui/motor_tab.py` (the `"# Startup: CN, MT, YA, LC, AC/DC, SP,
SH ABCD …"` log line just before the `startup_sequence` call), `tests/test_galil_protocol.py`
(`TestStartupSequence`, `make_galil`).

## What to build

NEC's spec for the slit controller sets stepper smoothing to 2. The app defines
`MOTOR_SMOOTHING = 2.0` but never sends it, and the config labels it `YB`, which on the DMC-41x3 is
full motor steps per revolution, not smoothing. Smoothing on the 41x3 is `KS`. After this ticket the
startup sequence sends it, so the controller runs NEC's setting rather than whatever it last held.

1. `GalilController.startup_sequence`: immediately after the `YA` command, send
   `KS <MOTOR_SMOOTHING>` repeated per axis, formatted with `:g` so 2.0 is sent as `2`. For four axes
   the command is exactly `KS 2,2,2,2`. Add `KS (stepper smoothing)` to the docstring's "Sends:" list
   after `YA`.
2. `src/rbl/config/hardware_config.py`: in the module docstring, "smoothing YB = 2.0" becomes
   "stepper smoothing KS = 2 (the e-mail says YB; on the DMC-41x3 YB is full steps per revolution)".
   The comment on `MOTOR_SMOOTHING` becomes `# KS: stepper smoothing, NEC spec`.
3. `src/rbl/gui/motor_tab.py`: the startup log line lists `KS` after `YA`.

## Acceptance criteria

- [x] Test `test_startup_sends_ks_after_ya` in `tests/test_galil_protocol.py` (in `TestStartupSequence`,
      built with `make_galil`): `startup_sequence(axes="AB", speed=900, accel=12800)` sends `"KS 2,2"`,
      after `"YA 2,2"` and before `"LC 1,1"`.
- [x] (no test: rewrites an existing test in place, proven by the gate) In `tests/test_galil_protocol.py`,
      test_full_sequence_order_and_content is rewritten in place under the same name: its expected
      list gains `"KS 2,2,2,2"` between `"YA 2,2,2,2"` and `"LC 1,1,1,1"`; nothing else changes.
- [x] (no test: CI's full suite) No test is deleted and every other test passes unchanged.

**What tests may fake:** the socket, through `make_galil`. `startup_sequence` is real.

**Out of scope:** speeds (147), the at-rest wait (148), `LC`, `AC`/`DC`, any other startup command.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_galil_protocol.py
```

pytest runs on the touched test files only; CI runs the full suite on the PR.

## Comments

2026-11-20: Implemented `KS <MOTOR_SMOOTHING>` in `GalilController.startup_sequence` after `YA` (formatted with `:g`), updated `hardware_config.py` docstring and `MOTOR_SMOOTHING` comment, and added `KS` to the `motor_tab.py` startup log line. Added `test_startup_sends_ks_after_ya` and updated `test_full_sequence_order_and_content` in `tests/test_galil_protocol.py`. All 54 tests in `tests/test_galil_protocol.py`, `ruff check .`, `check_tests_first.py`, and `type_gate.py` passed. Bench verification (`MG _KSA,_KSB,_KSC,_KSD`) remains in ticket 142.
