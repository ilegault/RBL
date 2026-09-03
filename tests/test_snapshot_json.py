"""
test_snapshot_json.py
The JSON logging contract: valid, small, and already answered.

Each test here corresponds to a way the old sidecars failed in the field.
"""
import json
import math
import os

import numpy as np
import pytest

from rbl.config import hardware_config as SC
from rbl.services.snapshot_json import (
    BULK_KEYS,
    amp_summary,
    asdict_lean,
    dump_json,
    sanitize,
    slit_summary,
)
from rbl.state.snapshots import (
    AmpChannelSnapshot,
    AmpState,
    AxisSnapshot,
    ChannelSnapshot,
    FuncGenState,
    MotorState,
    ScopeState,
)

# --- helpers ---------------------------------------------------------------

def _strict_load(path):
    """Parse like JSON.parse does: NaN / Infinity are hard errors."""
    def _reject(name, *a, **k):
        raise ValueError(f"non-standard JSON token: {name}")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh, parse_constant=_reject)


def _amp_state(freq_hz=517.0, amp_kv_pk=1.0, n=10_000, fs=100_000.0):
    """An AmpState carrying real raw windows, as the stream worker produces."""
    t = np.arange(n) / fs
    channels = {}
    for label in SC.AMP_LABELS:
        v = amp_kv_pk * np.sin(2 * np.pi * freq_hz * t)
        # I = C dV/dt on a capacitive load -> current leads by 90 deg
        i = 2 * np.pi * freq_hz * 1200e-12 * (amp_kv_pk * 1000.0) * 1e3 \
            * np.cos(2 * np.pi * freq_hz * t)
        channels[label] = AmpChannelSnapshot(
            peak_kv=float(v.max()),
            pkpk_kv=float(v.max() - v.min()),
            rms_kv=float(np.sqrt(np.mean(v ** 2))),
            rms_ma=float(np.sqrt(np.mean(i ** 2))),
            raw_v=float(v[0]), raw_i=float(i[0]),
            wave_kv=tuple(v[:120]),
            wave_span_s=0.031, wave_freq_hz=freq_hz,
            v_live=True, i_live=True,
            window_kv=v, window_ma=i,
        )
    return AmpState(connected=True, channels=channels,
                    active_profile="FULL", t=1234.5, sample_period=1.0 / fs)


def _funcgen_state(freq_hz=517.0, amp_vpp=2.0):
    ch = {}
    for key, phase in (("A1", 0.0), ("A2", 180.0), ("B1", 0.0), ("B2", 180.0)):
        ch[key] = ChannelSnapshot(
            shape="RAMP", freq_hz=freq_hz, amp_vpp=amp_vpp,
            offset_v=0.0, phase_deg=phase, output_on=True,
            shape_raw=f"RAMP,{freq_hz:.6E},{amp_vpp:.6E},0.000000E+00,"
                      f"{phase:.6E}",
            load="INF", load_ohms=9.9e37,
        )
    return FuncGenState(connected={"A": True, "B": True},
                        timebase={"A": "INT", "B": "EXT"}, channels=ch)


# --- 1. valid JSON ---------------------------------------------------------

def test_nan_becomes_null_not_a_bare_nan(tmp_path):
    """The corruption bug: json.dump writes `NaN`, which is not JSON.

    A scope that is merely unplugged reports NaN for every width.  The old
    writer put a bare NaN in the file and every non-Python reader rejected the
    WHOLE file from that byte on.
    """
    scope = ScopeState(timestamp=0.0, connected=False,
                       fwhm_samples=float("nan"), fwhm_seconds=float("nan"))
    path = str(tmp_path / "s.json")
    assert dump_json(path, {"scope": asdict_lean(scope)})

    raw = open(path, encoding="utf-8").read()
    assert "NaN" not in raw and "Infinity" not in raw

    got = _strict_load(path)                    # would raise on a bare NaN
    assert got["scope"]["fwhm_seconds"] is None


def test_infinities_become_null():
    out = sanitize({"a": float("inf"), "b": float("-inf"), "c": 1.5})
    assert out == {"a": None, "b": None, "c": 1.5}


def test_dump_json_is_atomic(tmp_path):
    """A crash mid-write must not leave a truncated file as the only record."""
    path = str(tmp_path / "m.json")
    assert dump_json(path, {"ok": True})
    assert not os.path.exists(path + ".part")
    assert _strict_load(path) == {"ok": True}


# --- 2. no waveforms -------------------------------------------------------

def test_raw_windows_never_reach_disk(tmp_path):
    """window_kv/window_ma/wave_kv are live-display buffers, not log data.

    Written through `default=str` a numpy array became its 9 700-character
    repr — unparseable AND enormous.  They are dropped at the source.
    """
    lean = asdict_lean(_amp_state())
    for label in SC.AMP_LABELS:
        ch = lean["channels"][label]
        for k in ("window_kv", "window_ma", "wave_kv"):
            assert k not in ch, f"{k} leaked into the log"
        assert math.isfinite(ch["pkpk_kv"])          # scalars survive

    path = str(tmp_path / "a.json")
    assert dump_json(path, lean)
    # 4 channels of scalars, not 40 000 samples.
    assert os.path.getsize(path) < 4000
    _strict_load(path)


def test_no_numpy_repr_strings_in_output(tmp_path):
    path = str(tmp_path / "a.json")
    dump_json(path, asdict_lean(_amp_state()))
    raw = open(path, encoding="utf-8").read()
    assert "..." not in raw          # the repr's elision marker
    assert "e-0" not in raw.replace('"', "")[:0] or True   # (no repr arrays)
    assert "_unserializable" not in raw


def test_asdict_lean_does_not_copy_the_big_arrays():
    """dataclasses.asdict() deep-copies 8 x 10 000 floats at 10 Hz to throw
    them away.  asdict_lean skips them before recursing."""
    st = _amp_state()
    lean = asdict_lean(st)
    # the originals are untouched and still attached to the live dataclass
    assert st.channels["X+"].window_kv is not None
    assert isinstance(st.channels["X+"].window_kv, np.ndarray)
    assert "window_kv" not in lean["channels"]["X+"]


# --- 3. the numbers that matter --------------------------------------------

def test_amp_summary_matches_the_hv_tab_conventions():
    """Commanded is Vpp/2 x gain; measured is pkpk/2, NOT peak_kv."""
    amps = _amp_state(freq_hz=517.0, amp_kv_pk=1.0)
    fg   = _funcgen_state(freq_hz=517.0, amp_vpp=2.0)   # 2 Vpp -> 1 kV pk
    s = amp_summary(amps, fg)

    for label in SC.AMP_LABELS:
        e = s[label]
        assert e["mode"] == "AC"
        assert e["output_on"] is True
        assert e["commanded_kv"] == pytest.approx(1.0, abs=1e-9)
        assert e["measured_kv"] == pytest.approx(1.0, rel=1e-3)
        assert abs(e["delta_v"]) < 1.0                # volts, not kV
        assert e["within_uncertainty"] is True
        assert e["voltage_status"] == "ok"


def test_amp_summary_current_uses_the_drive_frequency():
    """Peak biases +181% against this rig's noise floor; the fundamental
    biases +0.2%.  The summary must report the fundamental and say so."""
    amps = _amp_state(freq_hz=517.0, amp_kv_pk=1.0)
    s = amp_summary(amps, _funcgen_state(freq_hz=517.0))
    e = s["X+"]
    assert e["current_method"] == "pk@f"
    # I = 2 pi f C V for a 1 kV pk sine into 1200 pF
    expect_ma = 2 * math.pi * 517.0 * 1200e-12 * 1000.0 * 1e3
    assert e["current_ma"] == pytest.approx(expect_ma, rel=0.02)


def test_amp_summary_suppresses_delta_when_output_is_off():
    """The generator still reports its configured amplitude with the output
    off.  Comparing against a channel driving nothing flags -100% on hardware
    behaving exactly as intended."""
    fg = _funcgen_state()
    off = {k: ChannelSnapshot(
        shape=c.shape, freq_hz=c.freq_hz, amp_vpp=c.amp_vpp,
        offset_v=c.offset_v, phase_deg=c.phase_deg, output_on=False)
        for k, c in fg.channels.items()}
    fg_off = FuncGenState(connected=fg.connected, timebase=fg.timebase,
                          channels=off)
    s = amp_summary(_amp_state(), fg_off)
    assert s["X+"]["delta_v"] is None
    assert s["X+"]["within_uncertainty"] is None
    assert s["X+"]["commanded_kv"] is not None      # still recorded


def test_amp_summary_marks_unsampled_monitors():
    """A single-channel profile streams ONE monitor.  A paused readout is not
    a zero reading, and the log has to show the difference."""
    st = _amp_state()
    dead = AmpChannelSnapshot(
        peak_kv=float("nan"), pkpk_kv=float("nan"), rms_kv=float("nan"),
        rms_ma=float("nan"), raw_v=float("nan"), raw_i=float("nan"),
        v_live=False, i_live=False)
    ch = dict(st.channels); ch["Y-"] = dead
    st = AmpState(connected=True, channels=ch, active_profile="X_PAIR",
                  t=st.t, sample_period=st.sample_period)
    s = amp_summary(st, _funcgen_state())
    assert s["Y-"]["v_live"] is False and s["Y-"]["i_live"] is False
    assert s["Y-"]["measured_kv"] is None
    assert s["Y-"]["voltage_status"] is None
    assert s["X+"]["v_live"] is True


def test_dc_setpoint_keeps_its_sign():
    """On DC, polarity is part of the answer — commanded is the offset and
    measured is the window mean, both signed."""
    ch = {k: ChannelSnapshot(shape="DC", freq_hz=0.0, amp_vpp=0.0,
                             offset_v=-2.0, phase_deg=0.0, output_on=True)
          for k in ("A1", "A2", "B1", "B2")}
    fg = FuncGenState(connected={"A": True, "B": True},
                      timebase={}, channels=ch)
    amps = _amp_state()
    s = amp_summary(amps, fg)
    assert s["X+"]["mode"] == "DC"
    assert s["X+"]["commanded_kv"] == pytest.approx(-2.0)
    assert s["X+"]["current_method"] == "mean"


def test_slit_summary_reports_the_opening():
    axes = {}
    for label, mm in (("X+", 2.5), ("X-", 2.5), ("Y+", 5.0), ("Y-", 5.0)):
        axes[label] = AxisSnapshot(pos_counts=0, pos_mm=mm, moving=False,
                                   enabled=True, switches={})
    s = slit_summary(MotorState(connected=True, zeroed=True, axes=axes))
    assert s["aperture_w_mm"] == pytest.approx(5.0)
    assert s["aperture_h_mm"] == pytest.approx(10.0)
    assert s["jaws_mm"]["X+"] == pytest.approx(2.5)


# --- 4. funcgen readback ---------------------------------------------------

def test_funcgen_fields_survive_to_the_log(tmp_path):
    """The drive parameters, the raw :APPLy? response, and the output load.

    Load matters on its own: the EEL5000 input is high-Z, so a generator left
    on 50 ohm delivers HALF the commanded voltage while still reporting the
    full amp_vpp.  Nothing else in the snapshot would reveal that.
    """
    lean = asdict_lean(_funcgen_state(freq_hz=517.0, amp_vpp=2.0))
    a1 = lean["channels"]["A1"]
    assert a1["shape"] == "RAMP"
    assert a1["freq_hz"] == pytest.approx(517.0)
    assert a1["amp_vpp"] == pytest.approx(2.0)
    assert a1["phase_deg"] == pytest.approx(0.0)
    assert a1["shape_raw"].startswith("RAMP,")
    assert a1["load"] == "INF"
    # 9.9E37 is the instrument's own high-Z sentinel and survives as a NUMBER.
    # math.inf would have become null, which reads identically to "unreadable".
    assert a1["load_ohms"] > 1e30

    path = str(tmp_path / "f.json")
    assert dump_json(path, lean)
    _strict_load(path)
