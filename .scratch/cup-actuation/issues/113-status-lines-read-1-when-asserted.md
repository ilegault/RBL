# 113: Status lines read 1 when asserted (fix the inverted decode)

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Deletes tests:** tests/test_cup_status.py::test_explicit_closed_contact_is_zero_inversion, tests/test_cup_status.py::test_all_ones_word_returns_in_transit_and_auto_false

**Spec:** `.scratch/cup-actuation/spec.md` ("The hardware chain", the polarity paragraph)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `docs/adr/0003-commanded-and-confirmed-cup-position.md`
**Glossary:** `CONTEXT.md` — **Asserted**, **Confirmed position**, **In transit**, **AUTO mode**

## What to build

Today, with the cup physically IN and the controller in AUTO, the Faraday Cup tab reports
**confirmed OUT**. Commanding the cup works; reading its position back does not.

The pin mapping is right (FIO2 = IN, FIO3 = OUT, FIO4 = AUTO, bits 2/3/4 of `FIO_STATE`).
The polarity is wrong. On 2026-11-10 the developer read the real hardware in Kipling with
the cup IN and the controller in AUTO: **FIO2 = 1, FIO3 = 0, FIO4 = 1**, which is the raw
word `0b10100` = **20**. So a status line reads **1 when its condition is asserted**.
`decode_cup_status()` in `src/rbl/hardware/cup_status.py` assumes the opposite ("closed
is zero, the decoding function inverts") and turns that word into OUT with AUTO not
asserted.

After this ticket the same word decodes to IN with AUTO asserted, and the polarity lives
in one config constant, so a future wiring change is a one-line edit in
`src/rbl/config/cup_config.py` and not a code change.

The two tests on the **Deletes tests:** line are the only ones removed. Their names state
the old rule, and the two new tests in the second criterion replace them. Every other
test in this ticket is rewritten in place under its existing name.

Background in the bench record: `.scratch/cup-actuation/issues/12-bench-wiring-and-status-decoding.md`,
comment "2026-11-10 status polarity is inverted". Do not edit ticket 12.

## Acceptance criteria

- [ ] `src/rbl/config/cup_config.py` gains `CUP_STATUS_ASSERTED_LEVEL: int = 1`, placed
      directly under `CUP_STATUS_BIT_AUTO`, with a one-line comment citing the 2026-11-10
      Kipling reading. `decode_cup_status()` treats a status bit as asserted when it
      equals `CUP_STATUS_ASSERTED_LEVEL`. **The function must read that name at call time
      from its module globals** (no default argument, no mask or lookup table computed at
      import), because the test below swaps it with `monkeypatch` and a value captured at
      import would make that test pass against code that ignores the constant.
      Test: tests/test_cup_status.py::`test_asserted_level_constant_controls_the_decode`
      — monkeypatch `rbl.hardware.cup_status.CUP_STATUS_ASSERTED_LEVEL` to `0`; then
      `decode_cup_status(20)` returns `CupPosition.OUT` with `auto_mode is False`.
- [ ] The bench word decodes correctly. Tests use **integer literals**, never a word
      built from the bit constants or from a helper that encodes the same rule as the
      code, because a word derived from the rule under test cannot catch the rule being
      wrong. Test: tests/test_cup_status.py::`test_bench_word_cup_in_auto_decodes_in_and_auto`
      — `decode_cup_status(20)` (`0b10100`) is `CupStatus(CupPosition.IN, True)`.
      Test: tests/test_cup_status.py::`test_hardware_words_decode_per_line`, one test
      function looping over these literal cases:
      `24` (`0b11000`) → OUT, auto True; `16` (`0b10000`) → IN_TRANSIT, auto True;
      `28` (`0b11100`) → INDETERMINATE, auto True; `4` (`0b00100`) → IN, auto False;
      `0` → IN_TRANSIT, auto False; `0xFFE3` (every bit set except 2, 3, 4) → IN_TRANSIT,
      auto False, which proves bits outside 2–4 are ignored.
- [ ] Existing tests are rewritten in place, same names, to the new polarity, and pass:
      in `tests/test_cup_status.py`, `TestCupStatusPureDecodes._make_word` takes
      `in_asserted`, `out_asserted`, `auto_asserted` and sets an asserted bit to 1 and
      a non-asserted bit to 0, starting from `0x0000`. Callers in
      `test_decode_cup_in`, `test_decode_cup_out`, `test_decode_in_transit_neither_asserted`,
      `test_decode_indeterminate_both_asserted`, `test_decode_auto_asserted_and_not_asserted`
      and `test_unpacking_as_tuple` pass the same meaning under the new keyword names.
- [ ] The raw-word fixtures elsewhere are rewritten in place with the new polarity, as
      literals with a comment giving the binary: in `tests/test_cup_actuation.py` and
      `tests/test_faraday_cup_tab.py`, `RAW_STATUS_IN = 20`, `RAW_STATUS_OUT = 24`,
      `RAW_STATUS_TRANSIT = 16`, `RAW_STATUS_INDETERMINATE = 28`, and in
      `tests/test_faraday_cup_tab.py` `RAW_STATUS_NOT_AUTO = 4` (IN asserted, AUTO not).
      No test that uses them is edited otherwise, and every one still passes; that is
      the proof the tab and `Beamline` follow the decode with no other change. The named
      proofs: tests/test_faraday_cup_tab.py::`test_actuation_confirming_move_updates_both_indicators`,
      tests/test_faraday_cup_tab.py::`test_actuation_controller_not_in_auto_warning`,
      tests/test_faraday_cup_tab.py::`test_actuation_indeterminate_status_presents_as_fault`
      and tests/test_cup_actuation.py::`test_commanded_out_followed_by_confirming_status_word`.
      If any of them fails, the fault is in `src/`, not the test: fix the code and leave
      the assertion as it is.
- [ ] The old rule is gone from prose. Rewrite the polarity text in: the module and
      function docstrings of `src/rbl/hardware/cup_status.py`; the "Polarity:" block of
      the `src/rbl/config/cup_config.py` module docstring and the comment above
      `CUP_STATUS_BIT_IN`; the "Closed is zero:" block of the
      `src/rbl/hardware/labjack_driver.py` module docstring; and the paragraph starting
      "Polarity, stated once" in `.scratch/cup-actuation/spec.md` (keep its first
      sentence about the LJTRD and relay coil, which is still true). New wording, in
      substance: a status line reads 1 when its condition is asserted (bench,
      2026-11-10, cup IN in AUTO: FIO2 = 1, FIO3 = 0, FIO4 = 1); the level is
      `CUP_STATUS_ASSERTED_LEVEL`; a disconnected status cable floats all three lines
      high on the T7's pull-ups and so reads INDETERMINATE with AUTO asserted. Say
      *asserted*, not *closed*. Check by hand:
      `grep -rn -i "closed is zero\|decoding function inverts" src tests .scratch/cup-actuation/spec.md`
      prints nothing. (by hand)

**What tests may fake:** the LabJack and the stream feed (the existing
`tests/payloads.py` feed, as the tests already use it). `decode_cup_status()` itself
is never faked or patched, except the one `CUP_STATUS_ASSERTED_LEVEL` monkeypatch above.

**Out of scope:** the FIO0 "enable" → "interlock" rename (ticket 114). Moving any line
to a different FIO. Any UI to change the polarity or mapping (the developer chose a
config constant instead). Any change to relay drive or `src/rbl/state/cup_actuation_link.py`.
Ticket 12 and its checklist.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
