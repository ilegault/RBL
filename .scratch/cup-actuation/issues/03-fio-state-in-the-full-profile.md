# 03: Cup status on the stream, in the FULL profile

**What to build:** The three status contacts arrive in every stream window, on the
stream's own sample clock, in lockstep with the slit currents — costing no
command-response traffic and sharing the slit currents' timestamps.

After this ticket, a window coming out of the stream worker carries the cup's status
word alongside the analog channels, and every existing consumer of `window_ready` and
`raw_window_ready` still works.

**Blocked by:** 02

**Status:** done

**Read `docs/adr/0003-commanded-and-confirmed-cup-position.md` first.**
`docs/adr/0001-tests-first-and-no-muted-failures.md` is binding.

## FULL only, and the rate that costs

`FIO_STATE` joins the scan list of the **`FULL` profile only**. The other four profiles
(`WAVEFORM`, `AMP_PAIR`, `SINGLE_FAST`, `SINGLE_HIRES`) are amplifier diagnostics — they
are run during calibration and characterisation sweeps that never move the cup, and every
one of them already sits at the T7's aggregate ceiling, so adding a channel would cost
them sample density to carry a bit nobody reads there.

The T7 does not permit mid-stream scan-list changes, so this is a permanent addition to
`FULL`, not a runtime toggle.

`FULL` is 12 channels x 8 000 Hz = 96 000 S/s today, against a 100 000 S/s ceiling. A
thirteenth channel does not fit at that rate. **Change `FULL` to 7 500 Hz per channel**:
13 x 7 500 = 97 500 S/s, `window_samples` becomes 750, and oversampling at the 2 kHz fast
axis goes from ~4x to ~3.75x. Update the profile's `description` string and the
docstring claim to match — a stale "~4x" is worse than none.

Budget `FIO_STATE` as a full scan slot. It may cost the T7 less than an analog
conversion; 7 500 Hz is correct either way, and raising `FULL` back toward 8 000 Hz is a
separate change that needs datasheet evidence, not a guess made here.

## Structural requirements

- **Do not hardcode a Modbus address for `FIO_STATE`.** `_ain_address()` computes
  `int(name[3:]) * 2`, which raises `ValueError` on `"FIO_STATE"` (`int("_STATE")`).
  Replace it with a resolver that keeps the arithmetic for `AIN*` names and obtains a
  non-AIN name's address from `ljm.nameToAddress(name)[0]`, raising a clear error when
  LJM is unavailable. The `AIN*` branch must stay importable and testable without LJM.
- **The payload entry carries transitions, not a window average.** Windows are delivered
  at `GUI_REFRESH_HZ = 10`, so one window is 100 ms — twice the 50 ms contact debounce
  that ticket 06 depends on. A window mean or a single scalar would make that debounce
  unresolvable. The entry is a dict carrying the status word at the window's first scan,
  the word at its last scan, and a list of `(scan_index, word)` for every scan at which
  the word changed. With `sample_period` and `t` already in the payload, a consumer
  reconstructs each transition's absolute time to one sample.
- **`FIO_STATE` is explicitly `None` in every other profile**, the same "paused" marker
  the analog channels use, so a consumer can distinguish "not in this profile's scan
  list" from "read and found to be zero". The existing absent-channel loop iterates only
  `AMP_CHANNELS + LOGAMP_CHANNELS` and will not do this on its own.

- [x] `FIO_STATE` is in the `FULL` profile's scan list and in no other profile's
- [x] `FULL` runs at 7 500 Hz per channel; its `description` and the surrounding
      docstring state ~3.75x oversampling rather than ~4x
- [x] The import-time validation in `rbl/config/labjack_stream_config.py` accepts a
      non-AIN scan-list entry without weakening the aggregate-rate or per-resolution
      ceiling assertions, which still run against all five profiles
- [x] A scan-address resolver handles `AIN*` arithmetically and non-AIN names through
      `ljm.nameToAddress`; the `AIN*` path is unit-tested with no LJM installed
- [x] The window payload's `channels` dict carries a `FIO_STATE` entry with first word,
      last word, and a list of `(scan_index, word)` transitions
- [x] A test builds a synthetic window in which the status word changes mid-window and
      asserts the transition's scan index and word are both recovered
- [x] A test asserts `channels["FIO_STATE"] is None` for `WAVEFORM`, `AMP_PAIR`,
      `SINGLE_FAST` and `SINGLE_HIRES`
- [x] A test asserts every existing `window_ready` / `raw_window_ready` consumer still
      works with the new key present: the calibration runner, the load characterizer,
      the dynamic adjustment tab, and `AmpTrace.push`
- [x] `tests/payloads.py`'s `window_payload` builds the `FIO_STATE` entry, accepting an
      optional status word or transition series, defaulting to absent/None
- [x] Existing tests that assume a `FULL` window is 800 samples or an 8 000 Hz rate are
      found and corrected — grep the suite before opening the PR
- [x] The stream-worker self-test (`python -m rbl.hardware.labjack_stream_worker`) and
      the config self-test (`python -m rbl.config.labjack_stream_config`) both still pass
- [x] `ruff check .`, `python scripts/check_tests_first.py`, `python tools/type_gate.py`
      and `pytest --tb=short -q -n auto --dist loadfile` all pass

## Comments

### 2026-09-15 — Implementation Complete
- Added `FIO_STATE` to `FULL` profile scan list in `src/rbl/config/labjack_stream_config.py`, updated rate to 7 500 Hz per channel (97 500 S/s aggregate, 750 samples per 10 Hz window), and updated profile description and docstrings to reflect ~3.75x oversampling.
- Updated import-time scan list validation and self-test in `labjack_stream_config.py` to accept `FIO_STATE` exclusively in the `FULL` profile without loosening aggregate rate or resolution ceiling assertions.
- Added Modbus `channel_address` resolver in `src/rbl/hardware/labjack_stream_worker.py` (arithmetic for `AIN*` without LJM requirement; `ljm.nameToAddress` for non-AIN registers with clear error on missing LJM). Maintained `_ain_address` as an alias.
- Ensured digital register `FIO_STATE` is skipped during analog channel setup (`_RANGE`, `_NEGATIVE_CH`).
- Implemented transition tracking for `FIO_STATE` in `_build_payload`, publishing `first`, `last`, and `transitions` list of `(scan_index, word)` tuples when present in scan list, and `None` for absent profiles (`WAVEFORM`, `AMP_PAIR`, `SINGLE_FAST`, `SINGLE_HIRES`).
- Updated `tests/payloads.py` `window_payload` helper to accept `fio_state` (int, transition dict, or list of values/tuples), defaulting to `None`.
- Updated test suites assuming 8 000 Hz / 800 samples (`test_amp_waveform_fidelity.py`, `test_waveform_period.py`) to 7 500 Hz / 750 samples.
- Fixed `waveform_period.py` to isolate the first positive autocorrelation lobe, preventing sub-harmonic octave jumping for non-integer sample periods (such as 7.5 samples/cycle at 1 kHz).
- Added comprehensive unit tests in `tests/test_labjack_stream.py` covering config, address resolution, payload building, synthetic transitions, and consumer tolerance across `AmpTraceBuilder`, `CalibrationRunner`, `LoadCharacterizer`, and `DynamicAdjustmentTrial`.
- All 4 local gates passed: `ruff check .`, `python scripts/check_tests_first.py`, `python tools/type_gate.py` (0 hard errors, 139 soft errors matching ratchet), and full test suite (1771 passed, 0 failures).

