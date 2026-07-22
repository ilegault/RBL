"""
labjack_stream_config.py
Stream-mode configuration for the shared T7. Single source of truth.

Scan-list physical ordering
---------------------------
ALL channel lists in this module are ordered by ASCENDING PHYSICAL AIN NUMBER.
This is intentional and must not be changed without also updating the
de-interleave logic in rbl/hardware/labjack_stream_worker.py.

Why ascending order?
    The T7 sequences through its scan list left-to-right on every scan.
    Ascending order matches the physical wiring order on the terminal board,
    making it straightforward to verify the channel mapping visually without
    mentally reversing a list.

AIN assignments (from rbl/config/hardware_config.py):
    AIN0   NEC log amp — X+ jaw          (log-amp input, T7 body terminals)
    AIN1   NEC log amp — X- jaw
    AIN2   NEC log amp — Y+ jaw
    AIN3   NEC log amp — Y- jaw
    AIN4   spare  (configured in driver but excluded from all profiles)
    AIN5   spare  (configured in driver but excluded from all profiles)
    AIN6   EEL5000 Y-  CURRENT MONITOR   (1 V = 10 mA,  CB37 terminal board)
    AIN7   EEL5000 Y-  VOLTAGE MONITOR   (1 V = 1 kV)
    AIN8   EEL5000 Y+  CURRENT MONITOR
    AIN9   EEL5000 Y+  VOLTAGE MONITOR
    AIN10  EEL5000 X-  CURRENT MONITOR
    AIN11  EEL5000 X-  VOLTAGE MONITOR
    AIN12  EEL5000 X+  CURRENT MONITOR
    AIN13  EEL5000 X+  VOLTAGE MONITOR
"""

# ---------------------------------------------------------------------------
# T7 stream constraints — do NOT relax without verifying the datasheet
# ---------------------------------------------------------------------------

# Resolution index MUST be 0 or 1 to keep the 100 kS/s aggregate ceiling.
# Index >= 2 enables a noise-averaging filter that drastically reduces the
# maximum stream rate.  Index 8 (used in old command-response config) is
# incompatible with streaming.
STREAM_RESOLUTION_INDEX: int = 1

# +/-10 V range is REQUIRED on all channels:
#   • Keeps the maximum aggregate throughput (narrower ranges trigger the
#     anti-aliasing filter and lower the ceiling).
#   • Matches the EEL5000 current monitor peak: 10 V @ 100 mA / 4 ms transient.
STREAM_RANGE_VOLTS: float = 10.0

# Hard ceiling on aggregate sample rate for the T7 at resolution index 0 or 1.
T7_AGGREGATE_CEILING_HZ: int = 100_000

# ---------------------------------------------------------------------------
# GUI / consumer timing
# ---------------------------------------------------------------------------

# Waveform windows are delivered to the GUI at this rate.  Human-speed;
# completely decoupled from the hardware sample rate.
GUI_REFRESH_HZ: int = 10

# ---------------------------------------------------------------------------
# Channel groups — ascending physical AIN order (see module docstring)
# ---------------------------------------------------------------------------

# NEC log-amp inputs on the T7 body screw terminals.
# Ascending order: AIN0 (X+) → AIN1 (X-) → AIN2 (Y+) → AIN3 (Y-)
LOGAMP_CHANNELS: list = ["AIN0", "AIN1", "AIN2", "AIN3"]

# EEL5000 HV amplifier monitors on the CB37 terminal board.
# Ascending order: AIN6 (Y- current) → ... → AIN13 (X+ voltage)
# Pairs within each amplifier are (current, voltage) at consecutive even/odd AINs.
AMP_CHANNELS: list = [
    "AIN6",   # Y-  CURRENT MONITOR  — 1 V = 10 mA
    "AIN7",   # Y-  VOLTAGE MONITOR  — 1 V = 1 kV
    "AIN8",   # Y+  CURRENT MONITOR
    "AIN9",   # Y+  VOLTAGE MONITOR
    "AIN10",  # X-  CURRENT MONITOR
    "AIN11",  # X-  VOLTAGE MONITOR
    "AIN12",  # X+  CURRENT MONITOR
    "AIN13",  # X+  VOLTAGE MONITOR
]
# AIN4 and AIN5 are spare and intentionally absent from all profiles.

# ---------------------------------------------------------------------------
# Stream profiles
# ---------------------------------------------------------------------------
# Each profile defines which channels to stream and at what per-channel rate.
#
# Constraint:  len(scan_list) * per_channel_rate_hz <= T7_AGGREGATE_CEILING_HZ
#
# Scan lists use ascending physical AIN order (see module docstring).
# The de-interleave stride in LabJackStreamWorker equals len(scan_list) for
# the active profile and is asserted on every read.  Do not reorder these lists
# without updating that assertion.
#
# SWITCHING PROFILES requires a full  eStreamStop → reconfigure → eStreamStart
# cycle.  This is a HARDWARE CONSTRAINT: the T7 does not allow the scan list
# to be modified while a stream is running.  The service enforces this; never
# attempt a mid-stream scan-list change.

STREAM_PROFILES: dict = {
    "WAVEFORM": {
        # 8 channels × 12 500 Hz = 100 000 S/s  (at the T7 ceiling).
        # Best waveform fidelity: ~6× oversampling at a 2 kHz fast axis.
        # Log amps are NOT sampled; their payload entries are None so consumers
        # can display "paused" rather than showing stale numbers as live.
        "scan_list":           AMP_CHANNELS,          # ascending AIN6 → AIN13
        "per_channel_rate_hz": 12_500,
        "description": (
            "8 amp monitors only — 12.5 kS/s/ch.  "
            "~6× oversampling at 2 kHz fast axis.  Log amps paused."
        ),
    },
    "FULL": {
        # 12 channels × 8 000 Hz = 96 000 S/s  (4 000 S/s margin under ceiling).
        # All channels live; slightly coarser amp waveforms (~4× oversampling).
        # Scan list is purely ascending: log amps (AIN0-3) first, then amp
        # monitors (AIN6-13).  AIN4/5 are spare and intentionally skipped.
        "scan_list":           LOGAMP_CHANNELS + AMP_CHANNELS,  # AIN0-3, AIN6-13
        "per_channel_rate_hz": 8_000,
        "description": (
            "12 channels (4 log amp + 8 amp monitor) — 8 kS/s/ch.  "
            "~4× oversampling at 2 kHz fast axis.  All channels live."
        ),
    },
}

DEFAULT_PROFILE: str = "FULL"

# ---------------------------------------------------------------------------
# Derived per-profile helpers
# ---------------------------------------------------------------------------

def window_samples(profile_name: str) -> int:
    """Samples per channel per GUI refresh window for *profile_name*.

    This is also the ``scansPerRead`` argument passed to ``eStreamStart``,
    so each ``eStreamRead`` call returns exactly one GUI-refresh worth of data.
    """
    return STREAM_PROFILES[profile_name]["per_channel_rate_hz"] // GUI_REFRESH_HZ


def scan_list(profile_name: str) -> list:
    """Return a copy of the channel scan list for *profile_name*."""
    return list(STREAM_PROFILES[profile_name]["scan_list"])


# ---------------------------------------------------------------------------
# Validation — runs at import time so a mis-configured file fails immediately
# ---------------------------------------------------------------------------

assert STREAM_RESOLUTION_INDEX in (0, 1), (
    f"STREAM_RESOLUTION_INDEX={STREAM_RESOLUTION_INDEX} must be 0 or 1; "
    "higher values lower the T7 stream ceiling below 100 kS/s."
)
assert STREAM_RANGE_VOLTS == 10.0, (
    f"STREAM_RANGE_VOLTS={STREAM_RANGE_VOLTS} must be 10.0 V; "
    "other ranges trigger the anti-aliasing filter and lower the ceiling."
)

for _pname, _prof in STREAM_PROFILES.items():
    _n   = len(_prof["scan_list"])
    _r   = _prof["per_channel_rate_hz"]
    _agg = _n * _r
    assert _agg <= T7_AGGREGATE_CEILING_HZ, (
        f"Profile '{_pname}': {_n} ch × {_r} Hz = {_agg} S/s "
        f"exceeds T7 ceiling {T7_AGGREGATE_CEILING_HZ} S/s"
    )
    for _ch in _prof["scan_list"]:
        assert _ch in AMP_CHANNELS or _ch in LOGAMP_CHANNELS, (
            f"Profile '{_pname}': unknown channel '{_ch}'"
        )

del _pname, _prof, _n, _r, _agg, _ch


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Stream profiles (ascending physical AIN order):")
    for name, prof in STREAM_PROFILES.items():
        n    = len(prof["scan_list"])
        rate = prof["per_channel_rate_hz"]
        agg  = n * rate
        win  = window_samples(name)
        flag = "  <-- AT CEILING" if agg == T7_AGGREGATE_CEILING_HZ else ""
        print(f"  {name:10s}: {n:2d} ch x {rate:6d} Hz = {agg:7d} S/s  "
              f"window={win} samples{flag}")
        print(f"             scan_list = {prof['scan_list']}")
        assert agg <= T7_AGGREGATE_CEILING_HZ
        assert win == rate // GUI_REFRESH_HZ

    print(f"\n  DEFAULT_PROFILE         = {DEFAULT_PROFILE}")
    print(f"  STREAM_RESOLUTION_INDEX = {STREAM_RESOLUTION_INDEX}")
    print(f"  STREAM_RANGE_VOLTS      = {STREAM_RANGE_VOLTS} V")
    print(f"  GUI_REFRESH_HZ          = {GUI_REFRESH_HZ} Hz")
    print(f"\n  LOGAMP_CHANNELS ({len(LOGAMP_CHANNELS)}): {LOGAMP_CHANNELS}")
    print(f"  AMP_CHANNELS    ({len(AMP_CHANNELS)}): {AMP_CHANNELS}")

    all_ch = set(LOGAMP_CHANNELS) | set(AMP_CHANNELS)
    assert "AIN4" not in all_ch and "AIN5" not in all_ch, "Spare AIN4/5 in channel list"
    assert not (set(LOGAMP_CHANNELS) & set(AMP_CHANNELS)), "Overlap between channel groups"

    full_sl = scan_list("FULL")
    nums = [int(ch[3:]) for ch in full_sl]
    assert nums == sorted(nums), f"FULL scan list not in ascending order: {full_sl}"

    print(f"\n[OK] labjack_stream_config self-test passed  "
          f"(FULL window={window_samples('FULL')}, "
          f"WAVEFORM window={window_samples('WAVEFORM')})")
