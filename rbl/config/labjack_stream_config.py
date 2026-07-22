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
# maximum stream rate.  Index 8 gives the most effective bits (lowest noise)
# but caps the stream rate near ~1 kS/s.
#
# This is the DEFAULT resolution index for high-speed profiles that do not
# declare their own.  A profile may override it via a per-profile
# "resolution_index" key (see STREAM_PROFILES).  The single-channel
# high-resolution profile trades rate for a much higher index.
STREAM_RESOLUTION_INDEX: int = 1

# Maximum aggregate stream rate the T7 can sustain at each resolution index.
# Higher indices enable the noise-averaging filter, which lowers the ceiling.
# Values are conservative floors drawn from the T7 datasheet stream tables;
# they exist as a guardrail so a profile can never request a rate its chosen
# resolution index cannot deliver.  Only indices actually used by shipped
# profiles (1 and 8) need to be exact; the rest are safe under-estimates.
MAX_AGG_RATE_BY_RES: dict = {
    0: 100_000,
    1: 100_000,
    2:  52_000,
    3:  42_000,
    4:  25_000,
    5:  12_000,
    6:   6_000,
    7:   3_000,
    8:   1_000,
}

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

# Channels the user may target in single-channel mode.  Per the feature scope,
# this is exactly the 8 HV amplifier monitors (voltage + current) — the log
# amps are not offered as single-channel targets.
SINGLE_CHANNEL_CHOICES: list = list(AMP_CHANNELS)

# Default target when a single-channel profile is first selected: X+ voltage
# monitor (AIN13, the first amplifier in AMP_LABELS order).  The user picks a
# different target from the amp tab's channel selector at runtime.
DEFAULT_SINGLE_CHANNEL: str = "AIN13"

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
        "resolution_index":    1,
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
        "resolution_index":    1,
        "description": (
            "12 channels (4 log amp + 8 amp monitor) — 8 kS/s/ch.  "
            "~4× oversampling at 2 kHz fast axis.  All channels live."
        ),
    },
    "SINGLE_FAST": {
        # ONE user-selected amp monitor gets the entire 100 kS/s ceiling.
        # 1 channel × 100 000 Hz = 100 000 S/s → 10 000 samples per GUI window.
        # Resolution index stays at 1 (same effective bits as WAVEFORM/FULL);
        # the win here is temporal — maximum sample density for capturing fast
        # transients on a single reading.  "scan_list" is a placeholder default;
        # the live scan list is a single channel chosen at runtime (see
        # single_channel / channel_choices below).
        "scan_list":           [DEFAULT_SINGLE_CHANNEL],
        "per_channel_rate_hz": 100_000,
        "resolution_index":    1,
        "single_channel":      True,
        "channel_choices":     SINGLE_CHANNEL_CHOICES,
        "description": (
            "Single channel — 100 kS/s (max rate).  Full ceiling on one "
            "reading; 10 000 pts/window.  Best for fast transients."
        ),
    },
    "SINGLE_HIRES": {
        # ONE user-selected amp monitor at low rate but MAXIMUM resolution.
        # 1 channel × 1 000 Hz = 1 000 S/s at resolution index 8 (most
        # effective bits / lowest noise the T7 offers).  Trades sample density
        # for quiet, stable counts — best for precise DC-ish measurement.
        "scan_list":           [DEFAULT_SINGLE_CHANNEL],
        "per_channel_rate_hz": 1_000,
        "resolution_index":    8,
        "single_channel":      True,
        "channel_choices":     SINGLE_CHANNEL_CHOICES,
        "description": (
            "Single channel — 1 kS/s, resolution index 8 (max bits).  "
            "Lowest noise / highest counts.  Best for precise DC readings."
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


def resolution_index(profile_name: str) -> int:
    """Return the STREAM_RESOLUTION_INDEX to use for *profile_name*.

    Falls back to the module-level default for profiles that do not declare a
    per-profile ``resolution_index``.
    """
    return STREAM_PROFILES[profile_name].get(
        "resolution_index", STREAM_RESOLUTION_INDEX
    )


def is_single_channel(profile_name: str) -> bool:
    """True if *profile_name* streams a single, user-selectable channel."""
    return bool(STREAM_PROFILES[profile_name].get("single_channel", False))


def channel_choices(profile_name: str) -> list:
    """Channels the user may target for a single-channel profile.

    Returns a copy of the profile's ``channel_choices`` list, or an empty list
    for multi-channel profiles (whose scan list is fixed).
    """
    return list(STREAM_PROFILES[profile_name].get("channel_choices", []))


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
    _n     = len(_prof["scan_list"])
    _r     = _prof["per_channel_rate_hz"]
    _agg   = _n * _r
    _res   = resolution_index(_pname)
    _single = is_single_channel(_pname)

    assert _agg <= T7_AGGREGATE_CEILING_HZ, (
        f"Profile '{_pname}': {_n} ch × {_r} Hz = {_agg} S/s "
        f"exceeds T7 ceiling {T7_AGGREGATE_CEILING_HZ} S/s"
    )
    assert _res in MAX_AGG_RATE_BY_RES, (
        f"Profile '{_pname}': resolution_index={_res} out of range 0..8"
    )
    # A profile may not request a rate its resolution index cannot deliver.
    assert _agg <= MAX_AGG_RATE_BY_RES[_res], (
        f"Profile '{_pname}': {_agg} S/s exceeds the "
        f"{MAX_AGG_RATE_BY_RES[_res]} S/s ceiling at resolution index {_res}"
    )
    for _ch in _prof["scan_list"]:
        assert _ch in AMP_CHANNELS or _ch in LOGAMP_CHANNELS, (
            f"Profile '{_pname}': unknown channel '{_ch}'"
        )

    # Single-channel profiles must expose a non-empty, valid choice list.
    if _single:
        _choices = _prof.get("channel_choices", [])
        assert _choices, (
            f"Profile '{_pname}': single_channel profile needs 'channel_choices'"
        )
        for _ch in _choices:
            assert _ch in AMP_CHANNELS, (
                f"Profile '{_pname}': channel_choice '{_ch}' is not an amp monitor"
            )
        assert _prof["scan_list"][0] in _choices, (
            f"Profile '{_pname}': default scan_list channel "
            f"{_prof['scan_list'][0]} not in channel_choices"
        )

del _pname, _prof, _n, _r, _agg, _res, _single, _ch, _choices


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
        tag  = " [single]" if is_single_channel(name) else ""
        print(f"  {name:12s}: {n:2d} ch x {rate:6d} Hz = {agg:7d} S/s  "
              f"res={resolution_index(name)}  window={win} samples{flag}{tag}")
        print(f"             scan_list = {prof['scan_list']}")
        assert agg <= T7_AGGREGATE_CEILING_HZ
        assert agg <= MAX_AGG_RATE_BY_RES[resolution_index(name)]
        assert win == rate // GUI_REFRESH_HZ

    # Single-channel profile invariants.
    assert is_single_channel("SINGLE_FAST")  and is_single_channel("SINGLE_HIRES")
    assert not is_single_channel("FULL") and not is_single_channel("WAVEFORM")
    assert channel_choices("SINGLE_FAST") == AMP_CHANNELS
    assert set(channel_choices("SINGLE_HIRES")).issubset(set(AMP_CHANNELS))
    assert DEFAULT_SINGLE_CHANNEL in channel_choices("SINGLE_FAST")
    assert window_samples("SINGLE_FAST")  == 10_000   # 100 kS/s / 10 Hz
    assert window_samples("SINGLE_HIRES") == 100       # 1 kS/s  / 10 Hz
    assert resolution_index("SINGLE_HIRES") == 8
    assert resolution_index("SINGLE_FAST")  == 1

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
