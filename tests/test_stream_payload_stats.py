"""
Tests for the mean/std stats added to the per-channel window payload
(rbl/hardware/labjack_stream_worker.py._build_payload).

For a DC calibration sweep, rms is the wrong estimator: it discards sign, so
a -3 kV point and a +3 kV point produce the same RMS. mean is the correct DC
estimator and std the correct noise estimator. Both are computed from the
same raw window array already sliced out for peak/pk_pk/rms, in the same
pass, and must not disturb those existing keys.

No hardware / LJM required: _build_payload is pure array math, exercised
directly via a worker constructed with __new__.
"""
import numpy as np
import pytest

from rbl.config.labjack_stream_config import AMP_CHANNELS
from rbl.hardware.labjack_stream_worker import LabJackStreamWorker


def _worker(profile_name="WAVEFORM"):
    w = LabJackStreamWorker.__new__(LabJackStreamWorker)
    w._profile_name = profile_name
    return w


def _payload_for(col: np.ndarray):
    """Build a payload with a single amp channel driven by the given column."""
    w = _worker()
    target = AMP_CHANNELS[0]
    data = col.reshape(-1, 1)
    payload = w._build_payload([target], data, len(col), 0.0)
    return payload["channels"][target]


class TestMeanStd:
    def test_mean_of_simple_array(self):
        entry = _payload_for(np.array([1.0, 2.0, 3.0]))
        assert entry["mean"] == pytest.approx(2.0)
        print("[OK] mean of [1,2,3] == 2.0")

    def test_std_of_constant_array_is_zero(self):
        entry = _payload_for(np.full(100, 4.5))
        assert entry["std"] == pytest.approx(0.0, abs=1e-9)
        print("[OK] std of constant array == 0.0")

    def test_bipolar_symmetric_mean_zero_rms_nonzero(self):
        # -3 kV and +3 kV alternating: mean is the honest DC estimate (0),
        # RMS is not (it discards sign and reports the magnitude).
        col = np.array([-3.0, 3.0, -3.0, 3.0])
        entry = _payload_for(col)
        assert entry["mean"] == pytest.approx(0.0, abs=1e-9)
        assert entry["rms"] != pytest.approx(0.0, abs=1e-9)
        assert entry["rms"] == pytest.approx(3.0)
        print("[OK] mean of symmetric bipolar array == 0.0 while rms != 0.0")

    def test_existing_keys_unchanged(self):
        col = np.array([-1.0, 0.5, 2.0, -2.0])
        entry = _payload_for(col)
        assert entry["peak"] == pytest.approx(float(np.max(np.abs(col))))
        assert entry["pk_pk"] == pytest.approx(float(col.max() - col.min()))
        assert entry["rms"] == pytest.approx(float(np.sqrt(np.mean(col ** 2))))
        assert "waveform" in entry
        assert len(entry["waveform"]) == len(col)
        print("[OK] existing keys peak/pk_pk/rms unchanged in value and name")

    def test_std_matches_numpy_reference(self):
        rng = np.random.default_rng(42)
        col = rng.normal(loc=1.0, scale=0.7, size=500)
        entry = _payload_for(col)
        assert entry["mean"] == pytest.approx(float(np.mean(col)))
        assert entry["std"] == pytest.approx(float(np.std(col)))

    def test_stats_are_raw_volts_not_converted(self):
        # Same units as the existing keys (peak/pk_pk/rms) — no kV/mA scaling.
        col = np.full(10, 4.0)   # would clip if this were kV (AMP_MAX_KV=5)
        entry = _payload_for(col)
        assert entry["mean"] == pytest.approx(4.0)
