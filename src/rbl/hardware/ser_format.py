"""
ser_format.py
Pure serialization helpers for the SER v3 video format.

WHY THIS EXISTS
---------------
SER is the container format for the ZWO camera Master Recording. Its binary layout
follows the published SER v3 specification (Grischa Hahn / Chris Garry), widely
supported by astronomical image analysis tools such as SER Player, SER Viewer,
and AutoStakkert!.

A compiled Capture Process running as an independent program or a Python
sub-process must produce byte-for-byte identical streams (ADR 0008 decision 4).
By isolating the format logic into this pure module with no file handles, no
clocks, and no Qt dependencies, the header, trailer, and metadata encoding can be
thoroughly validated and proven against independent test decoders.

SER V3 STRUCTURE
----------------
1. Header: Exactly 178 bytes, little-endian:
   - FileID (14 bytes): ASCII b"LUCAM-RECORDER"
   - LuID (int32): 0 (reserved / unused)
   - ColorID (int32): Bayer/colour pattern identifier (e.g. 8 for RGGB)
   - LittleEndian (int32): 1 for little-endian pixel data
   - ImageWidth (int32): frame width in pixels
   - ImageHeight (int32): frame height in pixels
   - PixelDepthPerPlane (int32): 8 or 16 bits per pixel
   - FrameCount (int32): number of recorded frames (offset 38)
   - Observer (40 bytes): ASCII string, space-padded to 40 bytes
   - Instrument (40 bytes): ASCII string, space-padded to 40 bytes
   - Telescope (40 bytes): ASCII string, space-padded to 40 bytes
   - DateTime (int64): local time in 100 ns ticks since 0001-01-01 00:00:00
   - DateTime_UTC (int64): UTC time in 100 ns ticks since 0001-01-01 00:00:00

2. Frame Payload:
   Sequential raw frames in row-major order.
   - 8-bit: 1 byte per pixel
   - 16-bit: 2 bytes per pixel, unsigned little-endian (<u2)

3. Trailer:
   FrameCount little-endian int64 UTC timestamps, one per frame, in 100 ns ticks
   since 0001-01-01 00:00:00.
"""
import struct
from collections.abc import Sequence
from datetime import datetime

# Constants defined by SER v3 specification
SER_HEADER_BYTES: int = 178
SER_FILE_ID: bytes = b"LUCAM-RECORDER"
SER_LITTLE_ENDIAN_FLAG: int = 1
SER_FRAME_COUNT_OFFSET: int = 38

# SER Bayer color IDs
# 0 = MONO, 8 = RGGB, 9 = GRBG, 10 = GBRG, 11 = BGGR
_BAYER_COLOR_IDS: dict[str, int] = {
    "RG": 8,
    "GR": 9,
    "GB": 10,
    "BG": 11,
}

_EPOCH_ORDINAL: int = datetime(1, 1, 1).toordinal()
_TICKS_PER_SECOND: int = 10_000_000
_TICKS_PER_MICROSECOND: int = 10


def _pad_ascii(text: str, length: int) -> bytes:
    """Encode string to ASCII, truncate to length, and space-pad to fixed byte length."""
    encoded = text.encode("ascii", errors="replace")[:length]
    return encoded.ljust(length, b" ")


def pack_header(
    *,
    color_id: int,
    width: int,
    height: int,
    pixel_depth: int,
    frame_count: int,
    observer: str,
    instrument: str,
    telescope: str,
    date_local: int,
    date_utc: int,
) -> bytes:
    """Pack a 178-byte SER v3 header.

    Parameters are keyword-only to prevent transposition bugs.
    Raises ValueError if pixel_depth is not 8 or 16, or if width or height < 1.
    """
    if pixel_depth not in (8, 16):
        raise ValueError(f"pixel_depth must be 8 or 16, got {pixel_depth}")
    if width < 1:
        raise ValueError(f"width must be >= 1, got {width}")
    if height < 1:
        raise ValueError(f"height must be >= 1, got {height}")

    obs_bytes = _pad_ascii(observer, 40)
    inst_bytes = _pad_ascii(instrument, 40)
    tele_bytes = _pad_ascii(telescope, 40)

    return struct.pack(
        "<14s7i40s40s40sqq",
        SER_FILE_ID,
        0,
        color_id,
        SER_LITTLE_ENDIAN_FLAG,
        width,
        height,
        pixel_depth,
        frame_count,
        obs_bytes,
        inst_bytes,
        tele_bytes,
        date_local,
        date_utc,
    )


def pack_trailer(timestamps: Sequence[int]) -> bytes:
    """Pack trailer timestamps as little-endian int64 ticks (one per frame)."""
    if not timestamps:
        return b""
    return struct.pack(f"<{len(timestamps)}q", *timestamps)


def color_id_for_bayer(pattern: str) -> int:
    """Return the SER v3 ColorID corresponding to a two-letter Bayer pattern."""
    try:
        return _BAYER_COLOR_IDS[pattern]
    except KeyError:
        raise ValueError(
            f"Unknown Bayer pattern: {pattern!r}; expected 'RG', 'GR', 'GB', or 'BG'"
        ) from None


def ticks_from_datetime(dt: datetime) -> int:
    """Count of 100 ns ticks since 0001-01-01 00:00:00 of dt's own date and time fields.

    No timezone conversion is performed: caller passes local time for DateTime
    and UTC time for DateTime_UTC.
    """
    days = dt.toordinal() - _EPOCH_ORDINAL
    seconds = dt.hour * 3600 + dt.minute * 60 + dt.second
    return (days * 86400 + seconds) * _TICKS_PER_SECOND + dt.microsecond * _TICKS_PER_MICROSECOND
