"""
Pure tests for the Capture Process's photo and Dark Frame helpers.

WHY THIS EXISTS
---------------
Photo numbering and Dark Frame names decide whether an image from a run can be
overwritten or mistaken for another (ADR 0008 decision 6). They are checked here
without a camera or a child process.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from rbl.capture.camera import CameraInfo, CameraSettings
from rbl.capture.stills import (
    dark_file_name,
    next_photo_number,
    photo_cards,
    write_new_file,
)

WHEN = datetime(2026, 3, 4, 5, 6, 7, 891000, tzinfo=timezone.utc)


def test_next_photo_number_and_dark_names(tmp_path: Path) -> None:
    assert next_photo_number(tmp_path) == 0
    assert next_photo_number(tmp_path / "missing") == 0
    (tmp_path / "zwo_photo_002_0.fits").write_bytes(b"a")
    (tmp_path / "zwo_photo_010_3.fits").write_bytes(b"b")
    (tmp_path / "photo_005.png").write_bytes(b"c")
    assert next_photo_number(tmp_path) == 11

    name = dark_file_name("SER1", 10000, 100, 16, (8, 4, 64, 32), WHEN)
    assert name == "dark_exp10000us_gain100_raw16_x8y4w64h32_20260304T050607.fits"

    target = tmp_path / "SER1" / name
    first = write_new_file(target, b"first")
    second = write_new_file(target, b"second")
    third = write_new_file(target, b"third")
    assert first == target and target.read_bytes() == b"first"
    assert second.name == name.replace(".fits", "_2.fits")
    assert second.read_bytes() == b"second"
    assert third.name == name.replace(".fits", "_3.fits")


def test_photo_cards_describe_the_frame() -> None:
    info = CameraInfo("ASI585MC", "S1", "v", 3840, 2160, "RG")
    settings = CameraSettings(10000, 100, 16, (8, 4, 64, 32), 0, 50, 0)
    cards = {k: (v, c) for k, v, c in photo_cards(info, settings, 21.5, WHEN)}
    assert cards["EXPTIME"][0] == 0.01
    assert cards["GAIN"][0] == 100
    assert cards["BAYERPAT"][0] == "RG"
    assert (cards["XOFFSET"][0], cards["YOFFSET"][0]) == (8, 4)
    assert cards["CCD-TEMP"][0] == 21.5
    assert cards["DATE-OBS"][0] == "2026-03-04T05:06:07.891"
    assert cards["INSTRUME"][0] == "ASI585MC S1"
