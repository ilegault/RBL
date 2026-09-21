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

**Status:** ready-for-agent

## Files touched

- `scripts/visa_probe.py`: success criterion, de-duplicated summary, clearer per-resource lines

## Steps

1. **`scripts/visa_probe.py`, `run_child`.** Today it prints the `FOUND_MARKER` line for
   every listed resource. Print the `FOUND_MARKER` line for a resource **only after**
   `inst.query('*IDN?')` returns a non-empty string, and put the IDN on that line:
   `f"{FOUND_MARKER} {res} {idn}"`. For a resource that lists but fails, print
   `f"    {res}: LISTED BUT DID NOT ANSWER ({exc})"`, unchanged in spirit from today's
   failure lines. The protocol-mode query stays as it is.
2. **Same file, where `found` is parsed from the child output (~line 194).** Take the
   resource name as the first whitespace-separated token after `FOUND_MARKER`, so the
   appended IDN does not end up in the resource name.
3. **Same file, the `working` selection and the `USING:` banner (~line 196–220).** A
   backend is "working" only if at least one resource answered. If none did, print
   `NO BACKEND GOT AN ANSWER. Listed resources did not respond to *IDN?.` followed by
   `Check: instrument powered, GPIB selected (not RS-232), GPIB address matches, cable seated, instrument not inside a front-panel menu.`
   and return exit code `2` so a script calling it can tell the difference.
4. **Same file, the `--- Summary ---` block (~line 231–234).** Build `all_found` as a
   `sorted(set(...))` of resource names so a resource answered by three backends counts
   once. Print `answered` instead of `found`: `f"  {prefix:5s} {len(hits)} answered"`, or
   `NONE ANSWERED`.
5. **Same file, module docstring.** Add one paragraph: listing a GPIB resource proves
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
