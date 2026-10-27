"""preview_stretch: pure display helpers that never touch their input."""
import numpy as np
import pytest

from rbl.hardware.preview_stretch import (
    debayer_for_display,
    downscale_for_display,
    stretch_preview,
)


def _mosaic(shape=(16, 16)):
    rng = np.random.default_rng(1)
    return rng.integers(0, 65536, size=shape, dtype=np.uint16)


def test_never_modifies_input():
    cases = [
        lambda a: debayer_for_display(a, "RG"),
        lambda a: downscale_for_display(a, 8),
        lambda a: downscale_for_display(a, 1000),
        lambda a: stretch_preview(a, 16, "auto", 1.0),
        lambda a: stretch_preview(a, 16, "linear", 2.0),
    ]
    for call in cases:
        image = _mosaic()
        before = image.copy()
        out = call(image)
        assert np.array_equal(image, before)
        assert out is not image
        assert not np.shares_memory(out, image)


def test_linear_auto_flat_and_brightness():
    full16 = np.array([[0, 65535]], dtype=np.uint16)
    assert stretch_preview(full16, 16, "linear", 1.0).tolist() == [[0, 255]]
    full8 = np.array([[0, 255]], dtype=np.uint8)
    assert stretch_preview(full8, 8, "linear", 1.0).tolist() == [[0, 255]]
    assert stretch_preview(full8, 8, "linear", 1.0).dtype == np.uint8

    ramp = np.arange(0, 10001, dtype=np.uint16).reshape(1, -1)
    out = stretch_preview(ramp, 16, "auto", 1.0)
    low, high = np.percentile(ramp, [0.5, 99.5])
    assert out[0, int(round(low))] == 0
    assert out[0, int(round(high))] >= 254
    assert out[0, 0] == 0 and out[0, -1] == 255
    assert out.dtype == np.uint8 and out.shape == ramp.shape

    flat = np.full((4, 4), 1234, dtype=np.uint16)
    assert not stretch_preview(flat, 16, "auto", 1.0).any()

    mid = np.full((2, 2), 10000, dtype=np.uint16)
    assert np.array_equal(stretch_preview(mid, 16, "linear", 10.0),
                          stretch_preview(mid, 16, "linear", 4.0))
    assert np.array_equal(stretch_preview(mid, 16, "linear", 0.01),
                          stretch_preview(mid, 16, "linear", 0.25))
    assert stretch_preview(mid, 16, "linear", 4.0)[0, 0] > \
        stretch_preview(mid, 16, "linear", 1.0)[0, 0]


def test_unknown_stretch_mode_raises():
    with pytest.raises(ValueError):
        stretch_preview(_mosaic(), 16, "gamma", 1.0)


def test_downscale_keeps_aspect_and_small_images():
    big = np.zeros((100, 200), dtype=np.uint8)
    assert downscale_for_display(big, 100).shape == (50, 100)
    assert downscale_for_display(big, 200).shape == (100, 200)


_RED_SITE = {"RG": (0, 0), "GR": (0, 1), "GB": (1, 0), "BG": (1, 1)}


@pytest.mark.parametrize("pattern", sorted(_RED_SITE))
def test_debayer_puts_red_in_the_red_channel(pattern):
    row, col = _RED_SITE[pattern]
    mosaic = np.zeros((16, 16), dtype=np.uint16)
    mosaic[row::2, col::2] = 4000
    rgb = debayer_for_display(mosaic, pattern)
    assert rgb.shape == (16, 16, 3) and rgb.dtype == np.uint16
    means = [rgb[..., c].mean() for c in range(3)]
    assert means[0] > 10 * means[1]
    assert means[0] > 10 * means[2]


def test_debayer_unknown_pattern_raises():
    with pytest.raises(ValueError):
        debayer_for_display(np.zeros((4, 4), dtype=np.uint16), "XX")
