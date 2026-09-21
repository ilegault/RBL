# 11: Keithley 6482 driver speaks the 6482's real command set

**What to build:** Make `rbl.hardware.keithley6482_driver.Keithley6482` connect to a real
6482. Today it cannot: three of the commands it sends at connect do not exist on the
6482, and one of them is a query the instrument never answers, so every connect attempt
times out five seconds after the protocol-mode query succeeds.

Found on the bench on 2026-09-21 against a real 6482 (serial 4008420, firmware A01,
SCPI protocol, GPIB address 2). The transcript is in ticket 04's Comments and is the
authority for every command string below. The 6482 reference manual (6482-901-01,
Section 16, OUTPut and FORMat subsystems) agrees with it.

**Blocked by:** none

**Status:** done

## Why the existing tests passed

The mocked instrument in `tests/test_keithley6482_driver.py` (`mock_inst` fixture)
accepts every write and answers every unknown query with `"0"`. It models an instrument
that cannot reject anything, so it could not catch a wrong header. The real 6482 puts
`-113,"Undefined header"` in its error queue for an unknown command and **sends no
response at all** to an unknown query. That silence is the five-second timeout. Fixing
the fake is part of this ticket, not a side task: without it the corrected commands are
tested by the same harness that approved the wrong ones.

## Bench truth (2026-09-21)

| Command | 6482 response |
|---|---|
| `:SOURce1:STATe OFF`, `:SOURce2:STATe OFF` | `-113,"Undefined header"` |
| `:OUTPut1:STATe OFF`, `:OUTPut2:STATe OFF` | `0,"No error"` |
| `:SENSe1:FUNCtion 'CURRent:DC'` | `-113,"Undefined header"` |
| `:SENSe1:CURRent:DC:RANGe:AUTO ON` | `0,"No error"` |
| `:SENSe1:CURRent:DC:NPLCycles 1` | `0,"No error"` |
| `:SENSe1:MEDian:STATe OFF` | `0,"No error"` |
| `:SENSe1:AVERage:STATe OFF` | `0,"No error"` |
| `:FORMat:ELEMents READing,TIME,STATus` | `-102,"Syntax error"` |
| `:FORMat:ELEMents CURRent1,TIME,STATus` | `0,"No error"` |
| `:OUTPut1?` / `:OUTPut2?` | `'0'` / `'0'` |
| `:SOURce1:STATe?` | no response; `VI_ERROR_TMO`; queue gets `-113` |
| `:FORMat:ELEMents?` (after the above) | `'CURR1,TIME,STAT'` |
| `:READ?` | `'+4.827434E-11,+1.953328E+03,+0.000000E+00'` |

## Files touched

- `src/rbl/hardware/keithley6482_driver.py`: correct commands, per-command error-queue check at connect, docstring
- `src/rbl/config/cup_config.py`: `KEITHLEY_6482_DEFAULT_RESOURCE` becomes `"GPIB0::2::INSTR"`
- `tests/test_keithley6482_driver.py`: fake instrument rejects unknown headers the way the real one does; assertions updated; new tests

## Steps

1. **`tests/test_keithley6482_driver.py`, `mock_inst` fixture: write the failing tests first.**
   Replace the catch-all fake with one that models the real 6482:
   - Module-level `_ACCEPTED_WRITES: frozenset[str]` holds exactly the write strings that
     returned `0,"No error"` in the bench table, plus `"*CLS"`.
   - Module-level `_QUERY_RESPONSES: dict[str, str]` maps exact query strings to
     responses: `"*IDN?"` → `_MOCK_IDN`, `":SYSTem:MEP:STATe?"` → `"1"`,
     `":OUTPut1?"` → `"0"`, `":OUTPut2?"` → `"0"`,
     `":READ?"` → `"+4.827434E-11,+1.953328E+03,+0.000000E+00"`.
   - `_Fake6482` holds its own copy of the accepted set (`accepted_writes`) so a test can remove one entry.
   - The fake keeps an error queue (a list). A write not in `_ACCEPTED_WRITES` appends
     `'-113,"Undefined header"'`. A query not in `_QUERY_RESPONSES` and not
     `":SYSTem:ERRor?"` appends `'-113,"Undefined header"'` and raises
     `pyvisa.errors.VisaIOError(-1073807339)` (import the real `pyvisa.errors` for this; the
     exception class must be the real one). `":SYSTem:ERRor?"` pops and returns the
     oldest queue entry, or `'0,"No error"'` when the queue is empty. `"*CLS"` empties the queue.
   - Tests that need a different response (source active, 488.1 mode) override one
     entry of the response table, not the whole side effect. Build the fake as a small
     class (`_Fake6482`) with a `responses` dict attribute so a test can set
     `fake.responses[":OUTPut1?"] = "1"`.
   - Update `test_init_sends_required_configuration_commands` to assert the corrected
     strings from step 2 and to assert `":SOURce1:STATe OFF"`, `":SOURce2:STATe OFF"`,
     `":SENSe1:FUNCtion 'CURRent:DC'"` and `":FORMat:ELEMents READing,TIME,STATus"` are
     **not** written.
   - Update `test_init_raises_if_source1_is_active` / `..._source2_...` to set
     `":OUTPut1?"` / `":OUTPut2?"` to `"1"`.
   - Add `test_init_raises_naming_rejected_command`: remove one required write
     (`":SENSe1:CURRent:DC:NPLCycles 1"`) from that fake instance's accepted set;
     `Keithley6482(...)` raises `RuntimeError` whose message contains that exact command
     string and `-113`.
   - Add `test_init_clears_stale_errors_first`: pre-load the fake's error queue with
     `'-420,"Query UNTERMINATED"'` before construction; construction succeeds. (The
     bench showed a stale -420 left behind by an earlier timed-out query.)
   - Add `TestParseReading.test_bench_reading_from_real_6482`: `parse_reading("+4.827434E-11,+1.953328E+03,+0.000000E+00")`
     gives `current == pytest.approx(4.827434e-11)`, `timestamp == pytest.approx(1953.328)`,
     `status_word == 0`, `over_range is False`, `valid is True`.
   Run the file; the new and updated tests fail against the current driver. That failure
   is the point: it proves the harness can now see this class of bug.

2. **`src/rbl/hardware/keithley6482_driver.py`, `Keithley6482._init_instrument`.**
   Send this exact sequence, each through the checked write from step 3:
   ```
   *CLS                                    (plain write, not checked: clears stale errors)
   :OUTPut1:STATe OFF
   :OUTPut2:STATe OFF
   :SENSe1:CURRent:DC:RANGe:AUTO ON
   :SENSe1:CURRent:DC:NPLCycles 1
   :SENSe1:MEDian:STATe OFF
   :SENSe1:AVERage:STATe OFF
   :FORMat:ELEMents CURRent1,TIME,STATus
   ```
   then call `assert_sources_off()`. Delete the `:SENSe1:FUNCtion` line entirely: the 6482
   has no FUNCtion command because it only measures current.

3. **Same file, new method `Keithley6482._write_checked(self, cmd: str) -> None`.**
   Calls `self.write(cmd)`, then `resp = self.query(":SYSTem:ERRor?")`. Parse the leading
   integer before the first comma. If it is not `0`, raise
   `RuntimeError(f"Keithley 6482 rejected {cmd!r}: {resp}")`. Used only inside
   `_init_instrument`, never on the polling path (`read_raw` / `read_reading` are
   unchanged). This is a requirement, not a preference: a SCPI write that succeeds at
   the VISA layer tells you nothing about whether the instrument accepted it, and the
   only place a rejected configuration command is visible is the error queue.

4. **Same file, `Keithley6482.assert_sources_off`.** Query `":OUTPut1?"` and `":OUTPut2?"`
   instead of `":SOURce1:STATe?"` / `":SOURce2:STATe?"`. Keep the existing `RuntimeError`
   message text (`"voltage source safety assertion failed"`) so the existing match still holds.

5. **Same file, `parse_reading` docstring.** Change the expected-format line to
   `':FORMat:ELEMents CURRent1,TIME,STATus'` and the example to the bench string
   `'+4.827434E-11,+1.953328E+03,+0.000000E+00'`. No logic change: the bench response
   is already three comma-separated floats and the status word parses through `int(float(...))`.

6. **Same file, module docstring.** In "SAFETY REQUIREMENT" point 2 remove
   `':SENSe1:FUNCtion'`; in point 3 replace the `:SOURce` strings with
   `':OUTPut1:STATe OFF'`, `':OUTPut2:STATe OFF'`, asserted via `':OUTPut1?'` / `':OUTPut2?'`.
   In "MEASUREMENT & PARSING ARCHITECTURE" replace the elements line with
   `':FORMat:ELEMents CURRent1,TIME,STATus'`. Add a short paragraph under WHY THIS EXISTS
   headed `BENCH-VERIFIED COMMAND SET (2026-09-21)` stating: the original command set was
   written without a 6482 on the bench; `:SOURce<n>:STATe` and `:SENSe1:FUNCtion` are
   undefined headers on the 6482 and `READing` is not a valid FORMat element; an
   undefined query gets no reply, which surfaces as a VISA timeout rather than an error;
   so every connect-time write is followed by a `:SYSTem:ERRor?` check that names the rejected command.

7. **`src/rbl/config/cup_config.py`, `KEITHLEY_6482_DEFAULT_RESOURCE`.** Change to
   `"GPIB0::2::INSTR"` and add a one-line comment: `# Address set on the bench 6482's front panel (MENU > COMMUNICATION > GPIB); factory default is 25.`
   Update the example strings `GPIB0::14::INSTR` in the docstrings/tooltips of
   `src/rbl/gui/faraday_cup_tab.py` (line ~230), `src/rbl/state/picoammeter_link.py` (line ~74)
   and the `__main__`/example block of `keithley6482_driver.py` (line ~233) to `GPIB0::2::INSTR`.
   Do not change the resource strings inside tests; they are arbitrary.

## Verification

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```
All four pass. `pytest tests/test_keithley6482_driver.py -q` must show the tests added in
step 1 passing, and must have failed before steps 2–4 (say so in the PR text).

Bench check, for the developer after merge: launch the app, connect the picoammeter on
the Faraday Cup tab with `GPIB0::2::INSTR`, and confirm the log shows
`picoammeter_worker: connected to GPIB0::2::INSTR (KEITHLEY INSTRUMENTS INC.,MODEL 6482,...)`
followed by readings, with no timeout.

## Out of scope

- Which VISA library the app loads. The default `pyvisa.ResourceManager()` reaches the
  6482 on this PC once Keysight Connection Expert has scanned the address (bench, 2026-09-21).
- `discover()` and the worker's auto-discovery path.
- Channel 2, the voltage sources' levels, and anything to do with the cup actuation tickets.
- `scripts/visa_probe.py` (ticket 12).

## Comments

### 2026-09-21 — driver updated and verified against bench findings

- Updated `Keithley6482._init_instrument` to send bench-verified command sequence (`*CLS`, `:OUTPut1:STATe OFF`, `:OUTPut2:STATe OFF`, `:SENSe1:CURRent:DC:RANGe:AUTO ON`, `:SENSe1:CURRent:DC:NPLCycles 1`, `:SENSe1:MEDian:STATe OFF`, `:SENSe1:AVERage:STATe OFF`, `:FORMat:ELEMents CURRent1,TIME,STATus`), removing `:SENSe1:FUNCtion`.
- Added `_write_checked(cmd)` to verify connect-time writes via `:SYSTem:ERRor?`, raising `RuntimeError` if rejected.
- Updated `assert_sources_off` to query `:OUTPut1?` and `:OUTPut2?` instead of undefined `:SOURce<n>:STATe?`.
- Updated default VISA resource to `GPIB0::2::INSTR` in `cup_config.py` with explanatory comment, and updated example strings across `faraday_cup_tab.py`, `picoammeter_link.py`, and `keithley6482_driver.py`.
- Replaced test harness `mock_inst` with `_Fake6482` that models real instrument error queue and raises `VisaIOError(VI_ERROR_TMO)` on undefined queries. Verified tests failed against old driver before passing against updated implementation.
- All four local gates pass (ruff, check_tests_first, type_gate with 0 hard errors, and pytest with 1918 passing tests).
