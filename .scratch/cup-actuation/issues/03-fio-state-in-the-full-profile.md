# 03: Cup status on the stream, in the FULL profile

**What to build:** The three status contacts arrive in every stream window, on the
stream's own sample clock, in lockstep with the slit currents — costing no
command-response traffic and sharing the slit currents' timestamps.

After this ticket, a window coming out of the stream worker carries the cup's status
word alongside the analog channels, and every existing consumer of `window_ready` and
`raw_window_ready` still works.

**Blocked by:** 02

**Status:** ready-for-agent

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

- [ ] `FIO_STATE` is in the `FULL` profile's scan list and in no other profile's
- [ ] `FULL` runs at 7 500 Hz per channel; its `description` and the surrounding
      docstring state ~3.75x oversampling rather than ~4x
- [ ] The import-time validation in `rbl/config/labjack_stream_config.py` accepts a
      non-AIN scan-list entry without weakening the aggregate-rate or per-resolution
      ceiling assertions, which still run against all five profiles
- [ ] A scan-address resolver handles `AIN*` arithmetically and non-AIN names through
      `ljm.nameToAddress`; the `AIN*` path is unit-tested with no LJM installed
- [ ] The window payload's `channels` dict carries a `FIO_STATE` entry with first word,
      last word, and a list of `(scan_index, word)` transitions
- [ ] A test builds a synthetic window in which the status word changes mid-window and
      asserts the transition's scan index and word are both recovered
- [ ] A test asserts `channels["FIO_STATE"] is None` for `WAVEFORM`, `AMP_PAIR`,
      `SINGLE_FAST` and `SINGLE_HIRES`
- [ ] A test asserts every existing `window_ready` / `raw_window_ready` consumer still
      works with the new key present: the calibration runner, the load characterizer,
      the dynamic adjustment tab, and `AmpTrace.push`
- [ ] `tests/payloads.py`'s `window_payload` builds the `FIO_STATE` entry, accepting an
      optional status word or transition series, defaulting to absent/None
- [ ] Existing tests that assume a `FULL` window is 800 samples or an 8 000 Hz rate are
      found and corrected — grep the suite before opening the PR
- [ ] The stream-worker self-test (`python -m rbl.hardware.labjack_stream_worker`) and
      the config self-test (`python -m rbl.config.labjack_stream_config`) both still pass
- [ ] `ruff check .`, `python scripts/check_tests_first.py`, `python tools/type_gate.py`
      and `pytest --tb=short -q -n auto --dist loadfile` all pass
