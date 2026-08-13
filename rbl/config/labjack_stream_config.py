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
    AIN0   NEC log amp — X+ slit          (log-amp input, T7 body terminals)
    AIN1   NEC log amp — X- slit
    AIN2   NEC log amp — Y+ slit
    AIN3   NEC log amp — Y- slit
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

# Leading samples to throw away from the FIRST window after every
# eStreamStart, expressed as a duration so it means the same thing at every
# profile rate.
#
# A scan-list change cannot be made mid-stream, so every profile switch and
# every pair retarget is a full stop -> reconfigure -> start cycle.  Coming out
# of that cycle the T7's multiplexer and PGA are still settling, and the very
# first samples are not measurements of anything.  Switching WAVEFORM (8 ch @
# 12.5 kS/s) to AMP_PAIR (2 ch @ 50 kS/s) changes the per-channel dwell by 4x,
# which is exactly the case that provokes it.
#
# This matters beyond cosmetics: the calibration over-current interlock reads
# the current monitor on every window including during SETTLE, so without this
# trim the one moment the stream is guaranteed to be untrustworthy is a moment
# the interlock is watching — and it was aborting runs at 0 V commanded.
#
# SIZING.  Set empirically from this hardware, not from the datasheet: 1 ms,
# then 20 ms, then 100 ms, each raised because the interlock kept firing on
# stream switches at the previous value.  100 ms is exactly one GUI window, so
# at every profile the first window after a restart is now dropped whole and
# the second is the first one delivered.  The worker consumes the discard
# across however many windows it spans, so this may be raised past 100 ms
# without anything special happening.
#
# THE COST, STATED PLAINLY: this is dead time in which the over-current
# interlock sees nothing, and 100 ms is 25x the EEL5000's own 100 mA / 4 ms
# transient rating.  A genuine short occurring inside the trim goes unseen
# until the next window.  That is accepted because the trim only ever spans a
# scan-list change, which the sweep performs BETWEEN setpoints with the driven
# channel at or near 0 V — never while ramping.  Do not reach for this constant
# to silence an over-current seen during an actual drive; there the reading is
# real and the ladder is what should change.
#
# IF TRIPS PERSIST AT 100 ms, STOP RAISING THIS.  One whole window of settling
# is already far longer than a mux/PGA needs, and the escalation is evidence
# the cause may not be settling at all.  The worker prints, on every restart,
# the peak it discarded and the peak at the trim's trailing edge.  If that edge
# peak has decayed to the kept-window peak, the trim is long enough and the
# current being flagged is real.
STREAM_SETTLE_DISCARD_S: float = 0.100

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
# Amplifier pairs — the (current, voltage) monitors of ONE amplifier
# ---------------------------------------------------------------------------
# A calibration or amp-test sweep drives exactly one amplifier at a time and
# only ever analyses that amplifier's own two monitors.  Streaming all eight
# monitors during such a sweep spends 3/4 of the T7's aggregate budget on
# channels that are, by construction, sitting at zero — which costs the
# channel under test a factor of 4 in sample density.
#
# Ordering is ascending physical AIN, same rule as every other list here.
# For every amplifier the current monitor is the even AIN and the voltage
# monitor the odd one directly above it, so (current, voltage) is already
# ascending and no sort is needed.  Asserted at import.
AMP_PAIR_CHANNELS: dict = {
    "X+": ["AIN12", "AIN13"],   # current, voltage
    "X-": ["AIN10", "AIN11"],
    "Y+": ["AIN8",  "AIN9"],
    "Y-": ["AIN6",  "AIN7"],
}

# Default pair when a pair profile is first selected: X+, matching
# DEFAULT_SINGLE_CHANNEL's amplifier and AMP_LABELS order.
DEFAULT_AMP_PAIR: str = "X+"

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
    "AMP_PAIR": {
        # The two monitors of ONE amplifier get the whole ceiling.
        # 2 channels × 50 000 Hz = 100 000 S/s → 5 000 samples per channel
        # per GUI window, against WAVEFORM's 1 250.  Four times the sample
        # density on the only two channels a single-channel sweep actually
        # measures.
        #
        # This is the profile a DC or AC sweep should run in.  WAVEFORM
        # spends 75% of the T7's budget digitising three amplifiers that the
        # sweep has deliberately commanded to zero.
        #
        # Nyquist headroom: at 50 kS/s a 5 kHz drive is sampled 10x per
        # cycle, so the peak-detection the AC analysis depends on
        # ((max-min)/2) stays honest well past the frequencies the amplifier
        # can actually reproduce.
        #
        # "scan_list" is a placeholder default; the live scan list is the
        # pair chosen at runtime (see pair_channel / pair_choices below).
        "scan_list":           list(AMP_PAIR_CHANNELS[DEFAULT_AMP_PAIR]),
        "per_channel_rate_hz": 50_000,
        "resolution_index":    1,
        "pair_channel":        True,
        "pair_choices":        list(AMP_PAIR_CHANNELS.keys()),
        "description": (
            "One amplifier's current+voltage pair — 50 kS/s/ch (max rate).  "
            "5 000 pts/window/ch, 4x WAVEFORM.  For single-channel sweeps."
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


def is_pair_channel(profile_name: str) -> bool:
    """True if *profile_name* streams one amplifier's (current, voltage) pair.

    Distinct from ``is_single_channel``: a pair profile is targeted by AMP
    LABEL ("X+"), not by AIN name, because the two AINs always travel
    together and picking them independently is never what a sweep wants.
    """
    return bool(STREAM_PROFILES[profile_name].get("pair_channel", False))


def pair_choices(profile_name: str) -> list:
    """Amp labels the user may target for a pair profile ([] if not one)."""
    return list(STREAM_PROFILES[profile_name].get("pair_choices", []))


def pair_scan_list(amp_label: str) -> list:
    """Ascending-AIN [current, voltage] scan list for *amp_label*.

    Raises KeyError on an unknown label rather than returning a default:
    silently streaming the wrong amplifier during a sweep would produce data
    that looks valid and is attributed to the wrong channel, which is worse
    than a crash.
    """
    return list(AMP_PAIR_CHANNELS[amp_label])


def amp_for_pair_scan_list(scan_list_: list) -> str:
    """Reverse of pair_scan_list: which amp a 2-channel scan list belongs to.

    Returns "" if the list is not one of the defined pairs.
    """
    key = list(scan_list_)
    for amp, chans in AMP_PAIR_CHANNELS.items():
        if chans == key:
            return amp
    return ""


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

# Every pair must be [even AIN, odd AIN] with the odd one directly above --
# that is what makes (current, voltage) already ascending, which the
# de-interleave in LabJackStreamWorker relies on.
for _amp, _pair in AMP_PAIR_CHANNELS.items():
    assert len(_pair) == 2, f"AMP_PAIR_CHANNELS[{_amp}] must have 2 channels"
    _lo, _hi = int(_pair[0][3:]), int(_pair[1][3:])
    assert _lo % 2 == 0 and _hi == _lo + 1, (
        f"AMP_PAIR_CHANNELS[{_amp}]={_pair}: expected an even current AIN "
        f"followed by the odd voltage AIN directly above it"
    )
    for _ch in _pair:
        assert _ch in AMP_CHANNELS, (
            f"AMP_PAIR_CHANNELS[{_amp}]: '{_ch}' is not an amp monitor"
        )
assert sorted(c for p in AMP_PAIR_CHANNELS.values() for c in p) == \
       sorted(AMP_CHANNELS), (
    "AMP_PAIR_CHANNELS must partition AMP_CHANNELS exactly once each"
)
del _amp, _pair, _lo, _hi

for _pname, _prof in STREAM_PROFILES.items():
    _n     = len(_prof["scan_list"])
    _r     = _prof["per_channel_rate_hz"]
    _agg   = _n * _r
    _res   = resolution_index(_pname)
    _single = is_single_channel(_pname)
    _pair_p = is_pair_channel(_pname)

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

    # Pair profiles must name real amps and default to one of them.
    if _pair_p:
        assert not _single, (
            f"Profile '{_pname}': cannot be both single_channel and pair_channel"
        )
        _pchoices = _prof.get("pair_choices", [])
        assert _pchoices, (
            f"Profile '{_pname}': pair_channel profile needs 'pair_choices'"
        )
        for _amp in _pchoices:
            assert _amp in AMP_PAIR_CHANNELS, (
                f"Profile '{_pname}': pair_choice '{_amp}' is not a known amp"
            )
        assert _n == 2, (
            f"Profile '{_pname}': pair profile scan_list must be 2 channels, "
            f"got {_n}"
        )
        assert amp_for_pair_scan_list(_prof["scan_list"]), (
            f"Profile '{_pname}': default scan_list {_prof['scan_list']} is "
            f"not one of the AMP_PAIR_CHANNELS pairs"
        )

del _pname, _prof, _n, _r, _agg, _res, _single, _pair_p, _ch, _choices


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
    assert not is_single_channel("AMP_PAIR")

    # Pair profile invariants.  The 4x claim in AMP_PAIR's docstring is the
    # whole reason the profile exists, so it is asserted, not just asserted-to.
    assert is_pair_channel("AMP_PAIR")
    assert not is_pair_channel("WAVEFORM") and not is_pair_channel("SINGLE_FAST")
    assert pair_choices("AMP_PAIR") == ["X+", "X-", "Y+", "Y-"]
    assert window_samples("AMP_PAIR") == 5_000        # 50 kS/s / 10 Hz
    assert window_samples("AMP_PAIR") == 4 * window_samples("WAVEFORM")
    assert pair_scan_list("Y+") == ["AIN8", "AIN9"]
    assert amp_for_pair_scan_list(["AIN8", "AIN9"]) == "Y+"
    assert amp_for_pair_scan_list(["AIN9", "AIN8"]) == ""   # order matters
    assert amp_for_pair_scan_list(["AIN6"]) == ""
    for _a in pair_choices("AMP_PAIR"):
        _sl = pair_scan_list(_a)
        assert amp_for_pair_scan_list(_sl) == _a
        assert [int(c[3:]) for c in _sl] == sorted(int(c[3:]) for c in _sl)
    try:
        pair_scan_list("Z+")
    except KeyError:
        pass
    else:
        raise AssertionError("pair_scan_list must raise on an unknown amp")
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
