# 12: `visa_probe.py` reports success only when an instrument answers

**What to build:** Make `scripts/visa_probe.py` stop reporting a pass when nothing on
the bus replied.

On 2026-09-21 the probe printed `USING: default ... The default backend works. Nothing
to configure.` and `GPIB  3 found` in two runs where **no backend got an `*IDN?`
reply**: once with `VI_ERROR_RSRC_NFOUND` on open, once with `VI_ERROR_TMO` on the query.
Two faults cause this:

- A backend counts as "working" when `list_resources()` returns something. Keysight's
  VISA lists addresses from its saved instrument table, whether or not anything is
  there, and GPIB sessions open with nothing listening. Listing proves nothing.
- The summary counts the same resource once per backend (three backends listed
  `GPIB0::14::INSTR`, so it said "3 found").

**Blocked by:** none

**Status:** done

## Files touched

- `scripts/visa_probe.py`: success criterion, de-duplicated summary, clearer per-resource lines
- `tests/test_visa_probe.py`: unit tests for child output formatting, parsing, exit codes, and deduplication

## Steps

1. [x] **`scripts/visa_probe.py`, `run_child`.** Today it prints the `FOUND_MARKER` line for
   every listed resource. Print the `FOUND_MARKER` line for a resource **only after**
   `inst.query('*IDN?')` returns a non-empty string, and put the IDN on that line:
   `f"{FOUND_MARKER} {res} {idn}"`. For a resource that lists but fails, print
   `f"    {res}: LISTED BUT DID NOT ANSWER ({exc})"`, unchanged in spirit from today's
   failure lines. The protocol-mode query stays as it is.
2. [x] **Same file, where `found` is parsed from the child output (~line 194).** Take the
   resource name as the first whitespace-separated token after `FOUND_MARKER`, so the
   appended IDN does not end up in the resource name.
3. [x] **Same file, the `working` selection and the `USING:` banner (~line 196–220).** A
   backend is "working" only if at least one resource answered. If none did, print
   `NO BACKEND GOT AN ANSWER. Listed resources did not respond to *IDN?.` followed by
   `Check: instrument powered, GPIB selected (not RS-232), GPIB address matches, cable seated, instrument not inside a front-panel menu.`
   and return exit code `2` so a script calling it can tell the difference.
4. [x] **Same file, the `--- Summary ---` block (~line 231–234).** Build `all_found` as a
   `sorted(set(...))` of resource names so a resource answered by three backends counts
   once. Print `answered` instead of `found`: `f"  {prefix:5s} {len(hits)} answered"`, or
   `NONE ANSWERED`.
5. [x] **Same file, module docstring.** Add one paragraph: listing a GPIB resource proves
   nothing (Keysight lists remembered addresses; GPIB opens succeed with no listener), so
   the only success criterion is an `*IDN?` reply; cite the 2026-09-21 false pass.

## Verification

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```
All four pass (the change is under `scripts/`, which the tests-first gate exempts).

By hand, developer: run `.venv\Scripts\python scripts\visa_probe.py` twice. With the 6482
connected at `GPIB0::2::INSTR`: the summary reads `GPIB  1 answered` and the resource line
carries the 6482 IDN. With the GPIB cable unplugged: the banner reads `NO BACKEND GOT AN
ANSWER` and the exit code is 2 (`echo $LASTEXITCODE` in PowerShell).

## Out of scope

- Changing which VISA library the application loads.
- Probing USB instruments differently (ticket 10 covers the function generators and scope).

## Comments

### 2026-09-21 — Agent implementation and bench verification

Implemented all steps per specification:
1. Updated `run_child` in `scripts/visa_probe.py` so that `FOUND_MARKER` is only emitted when `*IDN?` returns a non-empty identification string, formatting the line as `f"{FOUND_MARKER} {res} {idn}"`. For resources that fail to open or fail/timeout on `*IDN?`, `f"    {res}: LISTED BUT DID NOT ANSWER ({exc})"` is printed.
2. Created `parse_child_found` helper extracting the first whitespace-separated token following `FOUND_MARKER`, ensuring resource identifiers are cleanly parsed without trailing IDN strings.
3. Updated `main()` so that a backend is only marked working if at least one resource answered. When no backends got an answer, printed the required warning banner and returned exit code 2.
4. Updated summary block to deduplicate resources with `sorted({r for _, _, f in working for r in f})`, displaying `f"  {prefix:5s} {len(hits)} answered"` (e.g. `GPIB  1 answered`) or `NONE ANSWERED`.
5. Updated module docstring with reasoning on why listing proves nothing and citing the 2026-09-21 false pass.
6. Added full test suite in `tests/test_visa_probe.py` covering child reporting, token extraction, exit code 2 behavior, and deduplicated answered summary.
7. Bench verification: Executed `python scripts/visa_probe.py` directly on bench hardware with the Keithley 6482 connected at `GPIB0::2::INSTR`. Verified output:
   `RESOURCE: GPIB0::2::INSTR KEITHLEY INSTRUMENTS INC.,MODEL 6482,4008420,A01   May 29 2012 09:36:59/A02  /E`
   `GPIB  1 answered     (Keithley picoammeter via the 82357B adapter)`
   `USB   NONE ANSWERED  (DG1022Z function generators / TDS 2012 scope)`
8. Gate verification:
   - `ruff check .` passed (0 issues).
   - `python scripts/check_tests_first.py` passed.
   - `python tools/type_gate.py` passed (0 hard, 139 soft <= 139 ratchet).
   - Full test suite passed (1926 passed, 0 failures).
