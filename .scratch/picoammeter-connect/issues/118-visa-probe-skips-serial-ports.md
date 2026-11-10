# 118: visa_probe skips serial ports and stops implying it tests the scope

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/faraday-cup-reader/spec.md`
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

## What to build

Run on 2026-11-10, `scripts/visa_probe.py` printed two misleading things:

1. `ASRL3::INSTR: LISTED BUT DID NOT ANSWER (VI_ERROR_RSRC_BUSY …)`. NI-VISA lists every
   COM port as an `ASRL` resource, and `run_child` opens each one and writes `*IDN?` to
   it. In RBL every serial instrument (the TDS 2012 scope through its USB-serial adapter,
   the two vacuum gauge controllers) is spoken to with pyserial, not VISA, and those
   instruments are probed only with their own read-only identity queries
   (`_CANDIDATES` in `src/rbl/hardware/serial_transport.py`). Writing `*IDN?` to them
   proves nothing and is not a query any of them is known to treat as harmless.
2. `USB   NONE ANSWERED  (DG1022Z function generators / TDS 2012 scope)`, which reads as
   "the scope is broken". The scope is RS-232 (`src/rbl/hardware/tds2012_driver.py`) and
   this script can never see it.

It also ends with `Record these lines in ticket 01's Comments.`, but ticket 01 is done.

## Acceptance criteria

- [x] In `run_child` in `scripts/visa_probe.py`, a resource whose name starts with
      `"ASRL"` (case-insensitive) is never opened. It prints exactly
      `f"    {res}: serial port, not probed (RBL talks to its serial instruments with pyserial, not VISA)"`
      and moves to the next resource. Test:
      tests/test_visa_probe.py::`test_run_child_skips_serial_ports` — copy
      `test_run_child_when_resource_answers`; `list_resources` returns
      `("ASRL3::INSTR", "GPIB0::2::INSTR")`; assert `open_resource` was called exactly
      once, with `"GPIB0::2::INSTR"`; the output contains
      `"ASRL3::INSTR: serial port, not probed"`; the return value is `0`.
- [x] `EXPECTED["USB"]` becomes `"DG1022Z function generators"`. After the per-category
      summary lines, `main()` prints exactly
      `"  TDS 2012 scope (BPM): RS-232 through a USB-serial adapter, not VISA. Not tested here; check it from the Beam Profiler tab."`
      Test: tests/test_visa_probe.py::`test_main_summary_says_scope_is_not_tested_here`
      — copy the setup of `test_main_summary_deduplicates_and_counts_answered`; assert
      that sentence is in the output and that no line starting with `"  USB"` contains
      `"TDS 2012"`.
- [x] The closing line becomes `"Record these lines in the bench ticket's Comments."`.
      Test: tests/test_visa_probe.py::`test_main_summary_says_scope_is_not_tested_here`
      (the same test as above) also asserts the output contains that line and does not
      contain `"ticket 01"`.
- [x] Existing tests in `tests/test_visa_probe.py` are not edited and pass, including
      `test_main_summary_deduplicates_and_counts_answered` (its `"USB   NONE ANSWERED"`
      assertion still holds); `git diff tests/test_visa_probe.py` shows only additions.
      (by hand)
- [x] The module docstring gains a short paragraph saying why `ASRL` resources are
      skipped, citing the 2026-11-10 run. (by hand)

**What tests may fake:** `pyvisa` (via `sys.modules`, as the existing tests do) and
`subprocess.run`. `run_child` and `main` are real.

**Out of scope:** adding a scope or serial probe to this script. Changing which backends
are probed. `.scratch/faraday-cup-reader/issues/10-existing-instruments-under-keysight-visa.md`
(the developer is told separately that its scope criteria assume USB).

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments

2026-11-10 summary: `run_child` skips `ASRL*` resources; `EXPECTED["USB"]` is DG1022Z only; `main()` prints the scope-not-tested sentence and the "bench ticket's Comments" line; the banner no longer says "ticket 01" (the new test asserts that string is absent from all output). Docstring paragraph added. Tests: `test_run_child_skips_serial_ports`, `test_main_summary_says_scope_is_not_tested_here`; existing tests untouched (diff is additions only). Gate verified on Linux; the script itself is only exercised through mocks, no Windows bench run.
