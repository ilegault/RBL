# 167: Status contacts read 0 when asserted, IN on FIO3 and OUT on FIO2

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/cup-status-fix/spec.md` (D1–D3 and the table)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `docs/adr/0003-commanded-and-confirmed-cup-position.md`
**Glossary:** `CONTEXT.md` — **Asserted**, **AUTO mode**, **In transit**
**Read first:** `decode_cup_status()` in `src/rbl/hardware/cup_status.py`; the constants block above `CUP_STATUS_ASSERTED_LEVEL` in `src/rbl/config/cup_config.py`; the "AUTO mode status" block in `FaradayCupTab` (search `lbl_auto_mode.setText`) in `src/rbl/gui/faraday_cup_tab.py`; `test_actuation_confirming_move_updates_both_indicators` in `tests/test_faraday_cup_tab.py` as the model for driving a move through `CupActuationFeed`.

## What to build

Today, with the controller in AUTO, the Faraday Cup tab says LOCAL, and an insertion often
raises "Move Fault: Command IN failed: contacts indeterminate" although the cup lands IN.
Both come from one wrong decode. The contacts close when asserted, so on the T7's pull-ups
an asserted line reads **0**; and FIO2 carries the OUT contact while FIO3 carries the IN
contact. After this ticket:

- `src/rbl/config/cup_config.py`: `CUP_STATUS_BIT_IN = 3`, `CUP_STATUS_BIT_OUT = 2`,
  `CUP_STATUS_BIT_AUTO = 4` (unchanged), `CUP_STATUS_ASSERTED_LEVEL = 0`. The comments on
  these lines cite the meter checks (2026-11-08: 4–12 closed in AUTO; 2026-11-20: 3–11
  closed with the cup IN). `decode_cup_status()` keeps reading the constants at call time
  from its module globals (no default argument, nothing computed at import): the
  monkeypatch test below depends on it.
- A word while the cup travels (both contacts open, word 12) decodes to `IN_TRANSIT`, so an
  insertion raises no move fault. Both contacts closed (word 0) is `INDETERMINATE`.
- The LOCAL warning in `FaradayCupTab` reads exactly
  `Controller not in AUTO (LOCAL mode) or status cable unplugged — remote commands will be ignored`
  (em dash U+2014), because an unplugged status cable now reads as LOCAL. The module
  docstring's sentence "remote commands will be accepted and ignored" says the same.

Tests use **integer literals** for words, never a word built from the bit constants: a
word derived from the rule under test cannot catch the rule being wrong.

## Acceptance criteria

- [x] Test `test_status_lines_read_zero_when_asserted`, in `tests/test_cup_status.py`:
      `decode_cup_status(4)` is `CupStatus(CupPosition.IN, True)`, `(8)` is
      `(CupPosition.OUT, True)`, `(12)` is `(CupPosition.IN_TRANSIT, True)`, `(20)` is
      `(CupPosition.IN, False)`. In the same file, rewrite in place under their names:
      test_line_names_and_bit_positions (asserts BIT_IN 3, BIT_OUT 2, BIT_AUTO 4, LEVEL 0);
      the `_make_word` helper (starts from `0b11100` and clears a bit to 0 when its
      condition is asserted; its callers keep their meaning);
      test_asserted_level_constant_controls_the_decode (monkeypatch the level to 1, then
      `decode_cup_status(4)` is OUT with auto_mode False);
      test_bench_word_cup_in_auto_decodes_in_and_auto (word 4, docstring cites 2026-11-20);
      test_hardware_words_decode_per_line (cases 8→OUT/True, 12→IN_TRANSIT/True,
      0→INDETERMINATE/True, 20→IN/False, 28→IN_TRANSIT/False, 0xFFE3→INDETERMINATE/True).
- [x] Test `test_unplugged_status_cable_reads_in_transit_and_not_auto`, in
      `tests/test_cup_status.py`: `decode_cup_status(28)` and `decode_cup_status(0xFFFF)`
      both return `CupStatus(CupPosition.IN_TRANSIT, False)`.
- [x] Test `test_both_contacts_closed_reads_indeterminate`, in `tests/test_cup_status.py`:
      `decode_cup_status(0)` is `(CupPosition.INDETERMINATE, True)` and
      `decode_cup_status(16)` is `(CupPosition.INDETERMINATE, False)`.
- [x] Test `test_insertion_through_transit_raises_no_move_fault`, in
      `tests/test_faraday_cup_tab.py`: with a `CupActuationFeed`, send word 8 at t=1.0,
      click `btn_insert`, send 12 at t=1.1, send 4 at t=1.2; then `lbl_confirmed` contains
      "IN" and `lbl_fault` text does not contain "Move Fault". In
      `tests/test_faraday_cup_tab.py` and `tests/test_cup_actuation.py`, rewrite in place
      the fixtures `RAW_STATUS_IN = 4`, `RAW_STATUS_OUT = 8`, `RAW_STATUS_TRANSIT = 12`,
      `RAW_STATUS_INDETERMINATE = 0`, and in `tests/test_faraday_cup_tab.py`
      `RAW_STATUS_NOT_AUTO = 20`, each with a comment giving FIO4 FIO3 FIO2 in binary. No
      test using them is edited otherwise; if one fails, fix `src/`, not the assertion.
- [x] Test `test_local_warning_names_unplugged_status_cable`, in
      `tests/test_faraday_cup_tab.py`: after word 20, `lbl_auto_mode.text()` equals the
      exact warning string above.
- [x] The old rule and mapping are gone from prose. Rewrite to "a status line reads 0 when
      asserted (contacts close to GND; meter 2026-11-08 and 2026-11-20); FIO3 carries IN
      (P1 3–11), FIO2 carries OUT (P1 2–10), FIO4 carries AUTO (P1 4–12)" in: the
      `src/rbl/config/cup_config.py` module docstring (the FIO2/FIO3 lines and the
      "Polarity:" block); the `src/rbl/hardware/cup_status.py` module and
      `decode_cup_status` docstrings (an unplugged cable now reads in transit, LOCAL); the
      `src/rbl/hardware/labjack_driver.py` module docstring and the comment above
      `FIO_DIRECTION`'s inputs; the module docstrings of
      `src/rbl/services/cup_session_writer.py` and `src/rbl/state/cup_actuation_link.py`;
      and in `.scratch/cup-actuation/spec.md` the "Polarity, stated once" paragraph, the
      hardware-chain table rows for FIO2 and FIO3, and the "Cable B, status" line. Say the
      2026-11-10 FIO4 reading was wrong. Check: `grep -rn "reads 1 when\|read 1 when\|FIO2 = IN\|FIO2 (cup IN)\|FIO2 (IN" src .scratch/cup-actuation/spec.md`
      prints nothing. (by hand)

**What tests may fake:** the LabJack (`ljm`) and the stream worker, as
`tests/test_cup_actuation.py` already fakes them. `decode_cup_status` and the tab are real.

**Out of scope:** `CONTEXT.md` (already updated on the tickets branch). The raw contact word
in the cup log (ticket 168). Debounce and timeout constants. `FIO_DIRECTION` itself (FIO2–4
stay inputs).

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_cup_status.py tests/test_cup_actuation.py tests/test_faraday_cup_tab.py
```

Run pytest on the touched test files only (implement-ticket-parallel, section 4). CI runs the full suite on the PR.

## Comments

2026-11-20: Implemented 0-when-asserted status decoding (`CUP_STATUS_BIT_IN = 3`, `CUP_STATUS_BIT_OUT = 2`, `CUP_STATUS_BIT_AUTO = 4`, `CUP_STATUS_ASSERTED_LEVEL = 0`), updated `FaradayCupTab` LOCAL/unplugged-cable warning string, updated docstrings and `.scratch/cup-actuation/spec.md` prose, and added/updated tests in `tests/test_cup_status.py`, `tests/test_cup_actuation.py`, and `tests/test_faraday_cup_tab.py` (including updating `test_manual_insertion_in_local_mode_issues_no_out_command` to use the new OUT/AUTO=8 and IN/LOCAL=20 words). Bench verification remains in ticket 174.
