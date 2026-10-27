"""
test_ser_format.py
Contract tests for SER v3 byte format packaging.

Tests parse the bytes using independent test-side readers written from the
published SER v3 specification, never using ser_format itself.
"""
import ast
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from rbl.hardware.ser_format import (
    SER_FILE_ID,
    SER_FRAME_COUNT_OFFSET,
    SER_HEADER_BYTES,
    SER_LITTLE_ENDIAN_FLAG,
    color_id_for_bayer,
    pack_header,
    pack_trailer,
    ticks_from_datetime,
)


def _decode_ser_header(raw: bytes) -> dict[str, object]:
    """Independent test-side reader for a 178-byte SER v3 header."""
    assert len(raw) >= SER_HEADER_BYTES
    header_bytes = raw[:SER_HEADER_BYTES]
    (
        file_id,
        lu_id,
        color_id,
        little_endian,
        image_width,
        image_height,
        pixel_depth,
        frame_count,
        observer,
        instrument,
        telescope,
        date_local,
        date_utc,
    ) = struct.unpack("<14s7i40s40s40sqq", header_bytes)

    return {
        "file_id": file_id,
        "lu_id": lu_id,
        "color_id": color_id,
        "little_endian": little_endian,
        "image_width": image_width,
        "image_height": image_height,
        "pixel_depth": pixel_depth,
        "frame_count": frame_count,
        "observer": observer,
        "instrument": instrument,
        "telescope": telescope,
        "date_local": date_local,
        "date_utc": date_utc,
    }


def test_header_fields_round_trip() -> None:
    """Header is 178 bytes; independent reader recovers every field and offset."""
    header = pack_header(
        color_id=9,
        width=1920,
        height=1080,
        pixel_depth=16,
        frame_count=42,
        observer="RBL Operator",
        instrument="ZWO ASI585MC SN12345",
        telescope="Station Diagnostic Port",
        date_local=638000000000000000,
        date_utc=638000000050000000,
    )

    assert len(header) == SER_HEADER_BYTES
    assert len(header) == 178

    fields = _decode_ser_header(header)
    assert fields["file_id"] == SER_FILE_ID
    assert fields["file_id"] == b"LUCAM-RECORDER"
    assert fields["lu_id"] == 0
    assert fields["color_id"] == 9
    assert fields["little_endian"] == SER_LITTLE_ENDIAN_FLAG
    assert fields["little_endian"] == 1
    assert fields["image_width"] == 1920
    assert fields["image_height"] == 1080
    assert fields["pixel_depth"] == 16
    assert fields["frame_count"] == 42
    assert fields["observer"] == b"RBL Operator".ljust(40, b" ")
    assert fields["instrument"] == b"ZWO ASI585MC SN12345".ljust(40, b" ")
    assert fields["telescope"] == b"Station Diagnostic Port".ljust(40, b" ")
    assert fields["date_local"] == 638000000000000000
    assert fields["date_utc"] == 638000000050000000

    # Frame count at SER_FRAME_COUNT_OFFSET (38)
    fc_at_offset = struct.unpack_from("<i", header, SER_FRAME_COUNT_OFFSET)[0]
    assert fc_at_offset == 42

    # Truncation of strings longer than 40 chars
    long_str = "A" * 50
    h_trunc = pack_header(
        color_id=8,
        width=100,
        height=100,
        pixel_depth=8,
        frame_count=1,
        observer=long_str,
        instrument=long_str,
        telescope=long_str,
        date_local=0,
        date_utc=0,
    )
    fields_trunc = _decode_ser_header(h_trunc)
    assert fields_trunc["observer"] == ("A" * 40).encode("ascii")
    assert fields_trunc["instrument"] == ("A" * 40).encode("ascii")
    assert fields_trunc["telescope"] == ("A" * 40).encode("ascii")

    # Validation: pixel_depth must be 8 or 16
    with pytest.raises(ValueError, match="pixel_depth"):
        pack_header(
            color_id=8,
            width=100,
            height=100,
            pixel_depth=12,
            frame_count=1,
            observer="",
            instrument="",
            telescope="",
            date_local=0,
            date_utc=0,
        )

    # Validation: width and height must be >= 1
    with pytest.raises(ValueError, match="width"):
        pack_header(
            color_id=8,
            width=0,
            height=100,
            pixel_depth=16,
            frame_count=1,
            observer="",
            instrument="",
            telescope="",
            date_local=0,
            date_utc=0,
        )
    with pytest.raises(ValueError, match="height"):
        pack_header(
            color_id=8,
            width=100,
            height=-1,
            pixel_depth=16,
            frame_count=1,
            observer="",
            instrument="",
            telescope="",
            date_local=0,
            date_utc=0,
        )


def test_frames_and_trailer_round_trip(tmp_path: Path) -> None:
    """Independent reader recovers pixel arrays and trailer timestamps for 16- and 8-bit."""
    for depth in (16, 8):
        width, height, num_frames = 20, 10, 5
        if depth == 16:
            frames = [
                np.arange(200, dtype=np.uint16).reshape((height, width)) + k * 1000
                for k in range(num_frames)
            ]
            frames_bytes = b"".join(f.astype("<u2").tobytes() for f in frames)
        else:
            frames = [
                (np.arange(200, dtype=np.uint8).reshape((height, width)) + k * 20).astype(np.uint8)
                for k in range(num_frames)
            ]
            frames_bytes = b"".join(f.tobytes() for f in frames)

        timestamps = [638000000000000000 + k * 20000000 for k in range(num_frames)]
        trailer = pack_trailer(timestamps)

        header = pack_header(
            color_id=8,
            width=width,
            height=height,
            pixel_depth=depth,
            frame_count=num_frames,
            observer="RBL",
            instrument="ASI",
            telescope="",
            date_local=timestamps[0],
            date_utc=timestamps[0],
        )

        file_path = tmp_path / f"test_{depth}bit.ser"
        file_path.write_bytes(header + frames_bytes + trailer)

        # Test-side reader parses file from scratch
        raw_file = file_path.read_bytes()
        hdr = _decode_ser_header(raw_file)
        assert hdr["frame_count"] == num_frames
        assert hdr["image_width"] == width
        assert hdr["image_height"] == height
        assert hdr["pixel_depth"] == depth

        bytes_per_pixel = 2 if depth == 16 else 1
        frame_bytes_len = width * height * bytes_per_pixel
        dtype_str = "<u2" if depth == 16 else "u1"

        for k in range(num_frames):
            offset = SER_HEADER_BYTES + k * frame_bytes_len
            frame_buf = raw_file[offset : offset + frame_bytes_len]
            read_arr = np.frombuffer(frame_buf, dtype=dtype_str).reshape((height, width))
            np.testing.assert_array_equal(read_arr, frames[k])

        trailer_offset = SER_HEADER_BYTES + num_frames * frame_bytes_len
        trailer_buf = raw_file[trailer_offset:]
        assert len(trailer_buf) == num_frames * 8
        read_timestamps = list(struct.unpack(f"<{num_frames}q", trailer_buf))
        assert read_timestamps == timestamps


def test_color_id_and_ticks() -> None:
    """Bayer mappings and datetime tick conversions follow the SER v3 specification."""
    assert color_id_for_bayer("RG") == 8
    assert color_id_for_bayer("GR") == 9
    assert color_id_for_bayer("GB") == 10
    assert color_id_for_bayer("BG") == 11
    with pytest.raises(ValueError, match="Unknown Bayer pattern"):
        color_id_for_bayer("XX")

    # Tick conversions: 100 ns ticks since 0001-01-01 00:00:00
    assert ticks_from_datetime(datetime(1, 1, 1)) == 0
    assert ticks_from_datetime(datetime(1970, 1, 1)) == 621355968000000000

    # One microsecond more adds exactly 10 ticks (1000 ns / 100 ns)
    assert ticks_from_datetime(datetime(1970, 1, 1, 0, 0, 0, 1)) == 621355968000000010

    # Timezone-aware UTC datetime produces the same count as naive with same fields
    naive_dt = datetime(2026, 10, 27, 15, 30, 45, 123456)
    aware_dt = datetime(2026, 10, 27, 15, 30, 45, 123456, tzinfo=timezone.utc)
    assert ticks_from_datetime(aware_dt) == ticks_from_datetime(naive_dt)


def test_imports_are_stdlib_and_numpy_only() -> None:
    """rbl.hardware.ser_format imports only standard library and numpy."""
    mod_path = Path(__file__).resolve().parent.parent / "src" / "rbl" / "hardware" / "ser_format.py"
    tree = ast.parse(mod_path.read_text(encoding="utf-8"))

    stdlib_modules = set(sys.stdlib_module_names)
    allowed_top_levels = stdlib_modules | {"numpy"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                assert top in allowed_top_levels, f"Disallowed import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                assert top in allowed_top_levels, f"Disallowed import from: {node.module}"
