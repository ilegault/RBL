# 02: T7 digital output drive, and status decoding as a pure function

**What to build:** The first digital I/O this repository has ever had. `labjack_driver.py`
reads analog inputs and nothing else today.

After this ticket a developer at the bench can put a meter on the LJTick-RelayDriver
outputs, call two methods, and watch the relays energise and release; and can read the
three status contacts back as a raw integer and see it decoded into a cup position. The
cup itself is not commanded in this ticket and no beamline is involved.

**Blocked by:** None (can start immediately)

**Status:** done

**Read `docs/adr/0003-commanded-and-confirmed-cup-position.md` first.**
`docs/adr/0001-tests-first-and-no-muted-failures.md` is binding.

## The hardware chain, for the line assignments

The CB37 terminal board is the hub; the T7 body screw terminals are not used.

| CB37 pin | Line | Role |
|---|---|---|
| 6  | FIO0 | LJTRD IOA -> relay 1 -> dry contact across ESM P1 **5-13**, enable cup OUT control |
| 24 | FIO1 | LJTRD IOB -> relay 2 -> dry contact across ESM P1 **1-9**, command cup OUT |
| 5  | FIO2 | cup **IN** status contact, other side to GND |
| 23 | FIO3 | cup **OUT** status contact, other side to GND |
| 4  | FIO4 | controller **AUTO mode** status contact, other side to GND |
| 1  | GND  | LJTRD GND and the common side of all three status contacts |
| 27 | Vs   | relay board VCC, input side only |

**Polarity, which is where this kind of work goes wrong.** LJTRD output-high closes its
switch, which pulls the relay board's active-low input down, which energises the relay.
A status contact that is **closed** pulls its FIO line to ground and therefore reads as
bit value **0**. Closed is zero. The decoding function inverts. Write that sentence into
the decode function's docstring.

**Cup OUT requires both closures.** Any loss of drive opens both and the cup returns IN,
where it intercepts the beam ahead of the specimen. This is a property of the wiring.
**Do not add a shutdown handler that drives the cup anywhere** — the absence of drive
*is* the safe state, and a software path that drove it would make the fail-safe depend
on software running.

## Structural requirements

- Status decoding is a **pure function in `rbl/hardware/`**, over a plain `int`, with no
  LabJack import on its call path. It is the only place a status word becomes a
  position. It must be fully testable with no T7 attached.
- The impossible states are handled **explicitly and named**. Neither contact asserted
  is `IN_TRANSIT` and is normal. Both asserted is `INDETERMINATE`, a wiring or
  controller fault, and is **never** resolved in favour of one — returning IN because IN
  was checked first is the specific bug this criterion exists to prevent.
- The driver exposes single-line writes, not a bitmask. A caller naming `FIO1` cannot
  accidentally clear `FIO0`.

## New configuration

All of it in `rbl/config/cup_config.py`, with the reasoning in the module docstring:

- the two output line names (enable, command-out)
- the three status bit positions (IN, OUT, AUTO)
- move confirmation timeout, `2.0` s
- contact debounce, `0.05` s

Use the existing naming style in that file. Do **not** reuse or lower
`CUP_ARM_DEBOUNCE_S` or `CUP_RELEASE_INTERVAL_S` — those own the current-inference path
and are sized for hand insertions lasting minutes. One set of numbers serving two
mechanisms is how the next person breaks both.

- [x] `labjack_driver.py` configures FIO0 and FIO1 as outputs and FIO2-FIO4 as inputs at
      connect, leaving both outputs de-asserted
- [x] A method writes one named digital line to a state without disturbing any other line
- [x] A method reads and returns the raw `FIO_STATE` integer
- [x] A new pure module under `rbl/hardware/` decodes a raw `FIO_STATE` int into a typed
      position (`IN` / `OUT` / `IN_TRANSIT` / `INDETERMINATE`) plus an AUTO-mode boolean
- [x] That module imports nothing from `labjack` and nothing from Qt
- [x] Decode tests, fed raw integers, cover: cup IN; cup OUT; in transit with neither
      asserted; both asserted returning `INDETERMINATE`; AUTO asserted; AUTO not
      asserted; and an explicit inversion case asserting that a **closed** contact reads
      as bit value **0**
- [x] A test asserts that decoding an all-ones word (every contact open) returns
      `IN_TRANSIT` and AUTO false, not a silently defaulted position
- [x] The new `cup_config.py` constants exist with their rationale in the module
      docstring, and the existing four inference constants are untouched
- [x] `labjack_driver.py`'s module docstring gains a `WHY THIS EXISTS` paragraph on the
      digital path, stating the closed-is-zero rule and the fails-into-the-beam property
- [x] Nothing in this ticket drives the cup on shutdown, on disconnect, or in an
      exception handler
- [x] `ruff check .`, `python scripts/check_tests_first.py`, `python tools/type_gate.py`
      and `pytest --tb=short -q -n auto --dist loadfile` all pass

## Comments

### 2026-09-15 — Implementation Complete
- Added digital I/O lines and actuation/status constants to `rbl/config/cup_config.py` (`CUP_ENABLE_LINE`, `CUP_COMMAND_OUT_LINE`, `CUP_ENABLE_OUT_LINE`, `CUP_STATUS_BIT_IN`, `CUP_STATUS_BIT_OUT`, `CUP_STATUS_BIT_AUTO`, `CUP_MOVE_CONFIRMATION_TIMEOUT_S`, `CUP_CONTACT_DEBOUNCE_S`), leaving existing inference constants untouched and documenting rationale in the module docstring.
- Built pure status decoder in `rbl/hardware/cup_status.py` with `CupPosition`, `CupStatus`, and `decode_cup_status()`, implementing the closed-is-zero inversion and handling `IN_TRANSIT` and `INDETERMINATE` states without Qt or LabJack imports.
- Updated `rbl/hardware/labjack_driver.py` to configure FIO0/FIO1 as outputs and FIO2-FIO4 as inputs via `FIO_DIRECTION` with inhibit masking and de-assert outputs at connect. Added single-line write method `write_digital()` and `read_fio_state()`. Added `WHY THIS EXISTS` documentation for the digital path, closed-is-zero rule, and fail-safe wiring.
- Added comprehensive unit tests in `tests/test_cup_status.py` and `tests/test_labjack_driver.py`.
- Verified all 4 gate checks locally: ruff clean, check_tests_first clean, type_gate passing (0 hard errors, ratchet maintained), and full test suite passing (1756 tests passed).

