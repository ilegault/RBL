"""
Ticket 46: the spike detector and reference capture (pure, synthetic data).

Every array comes from a seeded generator, so the statistical criteria are
deterministic. Seeds were chosen before running and are not tuned.
"""
import numpy as np
import pytest

from rbl.hardware.spike_detector import (
    MIN_RESOLVED_SAMPLES,
    MIN_SPIKE_SAMPLES,
    Reference,
    SpikeDetector,
    capture_reference,
    spike_threshold,
)

FS = 7500.0
DT = 1.0 / FS


def _baseline(rng, n, level, sigma):
    return level + sigma * rng.standard_normal(n)


def _feed_all(det, x, chunk, t0=0.0):
    out = []
    for i in range(0, len(x), chunk):
        out += det.feed(x[i:i + chunk], t0 + i * DT)
    return out


def test_quiet_monitor_threshold_is_twice_the_reference_and_a_6ma_spike_is_found_once():
    rng = np.random.default_rng(0)
    ref = capture_reference(_baseline(rng, int(60 * FS), 2.0, 0.1), DT)
    assert spike_threshold(ref) == pytest.approx(4.0, rel=0.02)

    x = _baseline(rng, int(2 * FS), 2.0, 0.1)
    start = 5000
    x[start:start + 8] = _baseline(rng, 8, 6.0, 0.1)     # about 1 ms
    spikes = _feed_all(SpikeDetector(ref, DT), x, 750)
    assert len(spikes) == 1
    assert spikes[0].peak_ma == pytest.approx(6.0, rel=0.05)
    assert spikes[0].start_s == pytest.approx(start * DT)
    assert spikes[0].duration_s == pytest.approx(8 * DT)


def test_noisy_monitor_threshold_is_reference_plus_five_noise():
    rng = np.random.default_rng(1)
    ref = capture_reference(_baseline(rng, int(60 * FS), 2.0, 1.0), DT)
    assert spike_threshold(ref) == pytest.approx(
        ref.current_ma + 5 * ref.noise_ma, rel=0.05)
    assert spike_threshold(ref) > 2 * ref.current_ma
    assert ref.noise_ma == pytest.approx(1.0, rel=0.05)


def test_one_hour_of_pure_noise_does_not_flood_the_log():
    rng = np.random.default_rng(2)
    ref = capture_reference(_baseline(rng, int(60 * FS), 2.0, 1.0), DT)
    det = SpikeDetector(ref, DT)
    chunk = int(0.1 * FS)                              # 100 ms windows
    n_spikes = 0
    for k in range(36_000):                            # one hour
        n_spikes += len(det.feed(_baseline(rng, chunk, 2.0, 1.0), k * 0.1))
    assert n_spikes < 5


def test_a_spike_straddling_two_windows_is_reported_once_with_full_duration():
    ref = Reference(current_ma=2.0, noise_ma=0.1)
    x = np.full(200, 2.0)
    x[97:105] = 9.0                                    # 3 samples, then 5
    det = SpikeDetector(ref, DT)
    first = det.feed(x[:100], 0.0)
    assert first == []
    second = det.feed(x[100:], 100 * DT)
    assert len(second) == 1
    assert second[0].duration_s == pytest.approx(8 * DT)
    assert second[0].start_s == pytest.approx(97 * DT)
    # and nothing more is reported by the window after it
    assert det.feed(np.full(100, 2.0), 200 * DT) == []


def test_a_spike_still_open_at_the_end_of_the_stream_is_not_reported_early():
    ref = Reference(current_ma=2.0, noise_ma=0.1)
    det = SpikeDetector(ref, DT)
    x = np.full(50, 2.0)
    x[-4:] = 9.0
    assert det.feed(x, 0.0) == []


@pytest.mark.parametrize("n_samples, lower_bound", [
    (3, True),
    (MIN_RESOLVED_SAMPLES - 1, True),
    (MIN_RESOLVED_SAMPLES, False),
    (20, False),
])
def test_lower_bound_flag_at_7p5_kilosamples(n_samples, lower_bound):
    ref = Reference(current_ma=2.0, noise_ma=0.1)
    x = np.full(300, 2.0)
    x[100:100 + n_samples] = 9.0
    (s,) = SpikeDetector(ref, DT).feed(x, 0.0)
    assert s.peak_is_lower_bound is lower_bound


def test_a_sub_100us_spike_is_a_lower_bound_even_with_enough_samples():
    ref = Reference(current_ma=2.0, noise_ma=0.1)
    dt = 10e-6                                         # 100 kS/s
    x = np.full(500, 2.0)
    x[100:109] = 9.0                                   # 90 us over 9 samples
    (s,) = SpikeDetector(ref, dt).feed(x, 0.0)
    assert s.duration_s < 100e-6
    assert s.peak_is_lower_bound is True


def test_rectangular_spike_10ma_above_reference_for_1ms_has_charge_10_uc():
    ref = Reference(current_ma=2.0, noise_ma=0.0)
    dt = 1e-4                                          # 10 kS/s
    x = np.full(100, 2.0)
    x[20:30] = 12.0
    (s,) = SpikeDetector(ref, dt).feed(x, 0.0)
    assert s.charge_uc == pytest.approx(10.0, rel=0.02)
    assert s.peak_ma == pytest.approx(12.0)


def test_a_single_sample_over_threshold_is_noise_not_a_spike():
    ref = Reference(current_ma=2.0, noise_ma=0.1)
    x = np.full(100, 2.0)
    x[50] = 9.0
    det = SpikeDetector(ref, DT)
    assert det.feed(x, 0.0) == []
    x[50:50 + MIN_SPIKE_SAMPLES] = 9.0
    assert len(det.feed(x, 0.0)) == 1


def test_one_sample_at_a_window_end_joins_its_neighbour_in_the_next():
    ref = Reference(current_ma=2.0, noise_ma=0.1)
    det = SpikeDetector(ref, DT)
    a = np.full(10, 2.0)
    a[-1] = 9.0
    b = np.full(10, 2.0)
    b[0] = 9.0
    assert det.feed(a, 0.0) == []
    (s,) = det.feed(b, 10 * DT)
    assert s.duration_s == pytest.approx(2 * DT)


def test_negative_excursions_count_by_magnitude():
    ref = Reference(current_ma=2.0, noise_ma=0.1)
    x = np.full(100, 2.0)
    x[40:50] = -9.0
    (s,) = SpikeDetector(ref, DT).feed(x, 0.0)
    assert s.peak_ma == pytest.approx(9.0)


def test_reference_needs_samples():
    with pytest.raises(ValueError):
        capture_reference(np.array([]), DT)
