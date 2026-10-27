"""
preview_stretch.py
Debayer, downscale and contrast-stretch a camera frame for the Preview only.

WHY THIS EXISTS
---------------
The Preview is for watching, never for intensity (CONTEXT.md "Preview"; ADR 0008
decision 6): a 16-bit frame has to become an 8-bit picture a person can look at,
and any stretch that does so throws intensity information away. That is fine on
the screen and fatal in a file, so the stretch lives here and nowhere near the
Master Recording.

The input array is the very frame that may also be queued for the Master
Recording. Every function therefore returns a NEW array and never modifies its
input; ``tests/test_preview_stretch.py`` checks that.

Pure: plain arrays in, plain arrays out. No Qt, no clock, no I/O.
"""
import cv2
import numpy as np

from rbl.config.capture_config import (
    PREVIEW_AUTO_HIGH_PCT,
    PREVIEW_AUTO_LOW_PCT,
    PREVIEW_BRIGHTNESS_MAX,
    PREVIEW_BRIGHTNESS_MIN,
)

# Four-letter aliases only: OpenCV's older two-letter names are offset from the
# sensor's pattern letters and are a known source of swapped red and blue.
_DEBAYER_CODES = {
    "RG": cv2.COLOR_BayerRGGB2RGB,
    "GR": cv2.COLOR_BayerGRBG2RGB,
    "GB": cv2.COLOR_BayerGBRG2RGB,
    "BG": cv2.COLOR_BayerBGGR2RGB,
}


def debayer_for_display(raw: np.ndarray, bayer_pattern: str) -> np.ndarray:
    """2-D uint8/uint16 mosaic -> HxWx3 RGB array of the same dtype."""
    try:
        code = _DEBAYER_CODES[bayer_pattern]
    except KeyError:
        raise ValueError(f"unknown Bayer pattern {bayer_pattern!r}") from None
    return np.asarray(cv2.cvtColor(np.ascontiguousarray(raw), code))


def downscale_for_display(image: np.ndarray, max_width: int) -> np.ndarray:
    """A copy of ``image``, shrunk (INTER_AREA, aspect kept) if wider than max_width."""
    height, width = image.shape[:2]
    if width <= max_width:
        return image.copy()
    new_height = max(1, round(height * max_width / width))
    return np.asarray(
        cv2.resize(image, (max_width, new_height), interpolation=cv2.INTER_AREA)
    )


def stretch_preview(image: np.ndarray, bit_depth: int, mode: str,
                    brightness: float) -> np.ndarray:
    """Map ``image`` to uint8 of the same shape for display. Input is untouched."""
    if mode not in ("linear", "auto"):
        raise ValueError(f"unknown stretch mode {mode!r}")
    gain = min(max(float(brightness), PREVIEW_BRIGHTNESS_MIN), PREVIEW_BRIGHTNESS_MAX)
    data = np.array(image, dtype=np.float64)  # a copy, never a view of the input
    if mode == "linear":
        scaled = data * (255.0 / float((1 << bit_depth) - 1))
    else:
        low, high = np.percentile(data, [PREVIEW_AUTO_LOW_PCT, PREVIEW_AUTO_HIGH_PCT])
        if high <= low:
            return np.zeros(image.shape, dtype=np.uint8)
        scaled = (data - low) * (255.0 / (high - low))
    return np.asarray(np.clip(scaled * gain, 0.0, 255.0).astype(np.uint8))
