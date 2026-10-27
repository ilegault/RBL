"""
zwo_settings_store.py
The ZWO camera's last applied settings and the selected camera kind, persisted.

WHY THIS EXISTS
---------------
The Capture Process applies exposure, gain, bit depth, ROI and keep-1-in-N, and
the operator expects the next session to open with the same values rather than
the factory defaults. The store also holds which camera is selected. Its default
camera kind is "usb" so the application behaves exactly as it did before the ZWO
camera existed until the operator picks it.

Same shape and warning behaviour as cup_settings_store.py: a rejected key falls
back to that field's default with one SettingsLoadWarning, an absent key takes
its default silently, load and save never raise, and a save is atomic
(temporary file plus os.replace) so a crash never leaves half a JSON file.
Defaults come exclusively from rbl.config.capture_config.

validate_settings is the only place the rules live, so the file and the panel
cannot disagree about what is allowed. The ROI is stored as
{"x", "y", "width", "height"}; null means the full sensor. Snapping to the
sensor happens in the Capture Process, not here.
"""
import dataclasses
import json
import logging
import math
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rbl.config.capture_config import (
    CAPTURE_KEEP_ONE_IN_N_DEFAULT,
    CAPTURE_KEEP_ONE_IN_N_MAX,
    PREVIEW_BRIGHTNESS_DEFAULT,
    PREVIEW_BRIGHTNESS_MAX,
    PREVIEW_BRIGHTNESS_MIN,
    PREVIEW_MODE_DEFAULT,
    ZWO_BIT_DEPTH_DEFAULT,
    ZWO_CAMERA_KIND_DEFAULT,
    ZWO_EXPOSURE_US_DEFAULT,
    ZWO_GAIN_DEFAULT,
)
from rbl.config.paths import ZWO_SETTINGS_STORE

logger = logging.getLogger(__name__)

STORE_PATH = ZWO_SETTINGS_STORE
README_TEXT = (
    "RBL rewrites this file whenever a ZWO camera setting changes. "
    "Edit it only while RBL is closed."
)

_ROI_KEYS = ("x", "y", "width", "height")


@dataclass(frozen=True)
class ZwoSettings:
    """Frozen ZWO camera settings. ``roi`` None means the full sensor."""

    camera_kind: str = ZWO_CAMERA_KIND_DEFAULT
    exposure_us: int = ZWO_EXPOSURE_US_DEFAULT
    gain: int = ZWO_GAIN_DEFAULT
    bit_depth: int = ZWO_BIT_DEPTH_DEFAULT
    roi: tuple[int, int, int, int] | None = None
    keep_one_in_n: int = CAPTURE_KEEP_ONE_IN_N_DEFAULT
    preview_mode: str = PREVIEW_MODE_DEFAULT
    brightness: float = PREVIEW_BRIGHTNESS_DEFAULT

    @classmethod
    def defaults(cls) -> "ZwoSettings":
        return cls()


@dataclass(frozen=True)
class SettingsLoadWarning:
    """One rejected setting: which key, what was found, why, and what replaced it."""

    key: str
    found: str
    reason: str
    fallback: object


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_kind(v: Any) -> str | None:
    return None if isinstance(v, str) and v in ("usb", "zwo") else 'must be "usb" or "zwo"'


def _check_exposure(v: Any) -> str | None:
    return None if _is_int(v) and v >= 1 else "must be an integer of at least 1"


def _check_gain(v: Any) -> str | None:
    return None if _is_int(v) and v >= 0 else "must be an integer of at least 0"


def _check_bit_depth(v: Any) -> str | None:
    return None if _is_int(v) and v in (8, 16) else "must be 8 or 16"


def _check_roi(v: Any) -> str | None:
    if v is None:
        return None
    if not isinstance(v, dict) or set(v) != set(_ROI_KEYS):
        return "must be null or an object with x, y, width and height"
    if not all(_is_int(v[k]) for k in _ROI_KEYS):
        return "x, y, width and height must be integers"
    if v["x"] < 0 or v["y"] < 0:
        return "x and y must be at least 0"
    if v["width"] < 8 or v["height"] < 2:
        return "width must be at least 8 and height at least 2"
    return None


def _check_keep(v: Any) -> str | None:
    if _is_int(v) and 1 <= v <= CAPTURE_KEEP_ONE_IN_N_MAX:
        return None
    return f"must be an integer from 1 to {CAPTURE_KEEP_ONE_IN_N_MAX}"


def _check_mode(v: Any) -> str | None:
    return None if isinstance(v, str) and v in ("auto", "linear") else 'must be "auto" or "linear"'


def _check_brightness(v: Any) -> str | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return "not a finite number"
    if not PREVIEW_BRIGHTNESS_MIN <= v <= PREVIEW_BRIGHTNESS_MAX:
        return f"must be between {PREVIEW_BRIGHTNESS_MIN} and {PREVIEW_BRIGHTNESS_MAX}"
    return None


_RULES: tuple[tuple[str, Callable[[Any], str | None]], ...] = (
    ("camera_kind", _check_kind),
    ("exposure_us", _check_exposure),
    ("gain", _check_gain),
    ("bit_depth", _check_bit_depth),
    ("roi", _check_roi),
    ("keep_one_in_n", _check_keep),
    ("preview_mode", _check_mode),
    ("brightness", _check_brightness),
)


def validate_settings(raw: dict) -> tuple[ZwoSettings, list[SettingsLoadWarning]]:
    """Turn raw values into ZwoSettings plus one warning per rejected key. Never raises."""
    if not isinstance(raw, dict):
        raw = {}
    defaults = ZwoSettings.defaults()
    accepted: dict[str, Any] = {}
    warnings: list[SettingsLoadWarning] = []
    for key, rule in _RULES:
        fallback = getattr(defaults, key)
        if key not in raw:
            accepted[key] = fallback
            continue
        value = raw[key]
        reason = rule(value)
        if reason is None:
            if key == "roi" and value is not None:
                value = tuple(value[k] for k in _ROI_KEYS)
            elif key == "brightness":
                value = float(value)
            accepted[key] = value
            continue
        accepted[key] = fallback
        try:
            found = repr(value)
        except Exception:  # a hostile __repr__ must not break loading
            found = "<unprintable>"
        warnings.append(SettingsLoadWarning(key, found, reason, fallback))
    return ZwoSettings(**accepted), warnings


def load_settings(path: Path | None = None) -> tuple[ZwoSettings, list[SettingsLoadWarning]]:
    """Load and validate the store. Never raises.

    A missing file gives the defaults and no warning; an unreadable, non-JSON or
    non-object file gives the defaults and one warning with key "_file".
    """
    target = Path(path if path is not None else STORE_PATH)
    if not target.exists():
        return ZwoSettings.defaults(), []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception as err:
        return ZwoSettings.defaults(), [SettingsLoadWarning(
            "_file", "<invalid>", f"Failed to read or parse JSON from {target}: {err}",
            "defaults")]
    if not isinstance(data, dict):
        return ZwoSettings.defaults(), [SettingsLoadWarning(
            "_file", repr(data), f"Root of {target} is not a JSON object", "defaults")]
    return validate_settings(data)


def save_settings(settings: ZwoSettings, path: Path | None = None) -> bool:
    """Atomically save ``settings``. Never raises; True on success."""
    target = Path(path if path is not None else STORE_PATH)
    data = dataclasses.asdict(settings)
    if settings.roi is not None:
        data["roi"] = dict(zip(_ROI_KEYS, settings.roi))
    data["_README"] = README_TEXT
    tmp_path: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix="zwo_camera_", suffix=".tmp")
        os.close(fd)
        tmp_path = Path(tmp_name)
        tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp_path, target)
        return True
    except Exception:
        logger.exception("Failed to save ZWO camera settings to %s", target)
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        return False
