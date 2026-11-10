"""
stills.py
Single-image products of the Capture Process: diagnostic photos and Dark Frames.

WHY THIS EXISTS
---------------
A photo is one acquired frame kept as its own FITS file (full bit depth, no video
codec); a Dark Frame is the mean of several frames taken with the lens covered,
saved into a per-camera library so a later analysis can subtract it (ADR 0008
decision 6, spec D2 and D7). Both must carry the settings they were taken with in
the header, because a dark is only useful against frames with the same exposure,
gain, bit depth and ROI; the Dark Frame file name repeats those so a library can
be matched by listing a folder.

Nothing here may ever overwrite an earlier image: a photo or a dark is evidence
from a run. Every file is opened with ``"xb"``; a Dark Frame whose name already
exists (two in the same second) gets ``_2``, ``_3`` ... before the extension, and
photo numbers only ever count up from the highest on disk.

This module takes plain values and touches only the folder it is given. It has no
Qt and no process state.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from rbl.capture.camera import CameraInfo, CameraSettings

_PHOTO_RE = re.compile(r"^zwo_photo_(\d+)_\d+\.fits$")


def photo_cards(
    info: CameraInfo,
    settings: CameraSettings,
    temperature_c: float,
    date_obs_utc: datetime,
) -> list[tuple[str, object, str]]:
    """FITS header cards describing how a frame was taken."""
    when = date_obs_utc.astimezone(timezone.utc)
    date_obs = when.strftime("%Y-%m-%dT%H:%M:%S") + f".{when.microsecond // 1000:03d}"
    return [
        ("EXPTIME", settings.exposure_us / 1_000_000.0, "exposure time [s]"),
        ("GAIN", settings.gain, "camera gain"),
        ("BAYERPAT", info.bayer_pattern, "Bayer pattern"),
        ("XOFFSET", settings.roi[0], "ROI origin x [px]"),
        ("YOFFSET", settings.roi[1], "ROI origin y [px]"),
        ("CCD-TEMP", float(temperature_c), "sensor temperature [C]"),
        ("DATE-OBS", date_obs, "UTC start of frame arrival"),
        ("INSTRUME", f"{info.model} {info.serial}", "camera"),
    ]


def next_photo_number(folder: Path) -> int:
    """One more than the highest ``NNN`` of ``zwo_photo_NNN_K.fits`` in ``folder``, or 0."""
    highest = -1
    if folder.is_dir():
        for path in folder.iterdir():
            m = _PHOTO_RE.match(path.name)
            if m:
                highest = max(highest, int(m.group(1)))
    return highest + 1


def dark_file_name(
    serial: str,
    exposure_us: int,
    gain: int,
    bit_depth: int,
    roi: tuple[int, int, int, int],
    when_utc: datetime,
) -> str:
    """Library file name of a Dark Frame (placed by the caller under ``<folder>/<serial>/``)."""
    x, y, w, h = roi
    stamp = when_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return (
        f"dark_exp{exposure_us}us_gain{gain}_raw{bit_depth}_x{x}y{y}w{w}h{h}_{stamp}.fits"
    )


def write_new_file(path: Path, data: bytes) -> Path:
    """Write ``data`` to ``path`` without ever overwriting; returns the path used.

    A name that exists gets ``_2``, ``_3`` ... before the extension.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 1
    candidate = path
    while True:
        try:
            with open(candidate, "xb") as fh:
                fh.write(data)
            return candidate
        except FileExistsError:
            n += 1
            candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
