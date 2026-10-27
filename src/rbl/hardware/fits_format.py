"""
fits_format.py
Pure serialization helper for FITS primary HDUs.

WHY THIS EXISTS
---------------
Flexible Image Transport System (FITS) is the standard format used by RBL where
one image is saved as one file: diagnostic photos taken during runs and Dark
Frames saved into the calibration darks library (ADR 0008 decision 4, spec D7).
Astronomy analysis tools (Fiji / ImageJ, SAOImageDS9, AstroImageJ, Astropy) read
FITS files out of the box.

The Capture Process writes FITS photos and dark frames without depending on
external third-party astronomy libraries like astropy. By implementing this pure,
deterministic byte serializer in rbl.hardware with standard library and numpy
alone, we ensure that both the Python Capture Process and any future compiled
replacement write byte-identical FITS files with zero GUI or heavy runtime
dependencies.

FITS HDU STRUCTURE
------------------
1. Fixed 2880-byte block structure throughout both header and data.
2. Header cards are 80 ASCII characters each.
3. First five cards must be mandatory structural keywords in order:
   SIMPLE = T, BITPIX, NAXIS = 2, NAXIS1 (columns/width), NAXIS2 (rows/height).
4. For uint16 data:
   FITS does not natively support unsigned 16-bit integers in BITPIX; it stores
   them as signed 16-bit big-endian integers (BITPIX = 16) with physical value
   offset defined by BZERO = 32768 and BSCALE = 1 (value - 32768 on disk).
5. For uint8 data: BITPIX = 8, no BZERO card.
6. For float32 data: BITPIX = -32 (IEEE 754 single precision big-endian).
7. Caller cards follow the mandatory cards, terminated by the END card.
8. Unused header bytes after END in the 2880-byte header block are ASCII spaces.
9. Data is stored in row-major big-endian order, zero-padded to a multiple of 2880 bytes.
"""
from collections.abc import Sequence

import numpy as np

_BLOCK_SIZE: int = 2880
_CARD_SIZE: int = 80


def _format_card(keyword: str, value: object, comment: str) -> str:
    """Format a single 80-character fixed-format FITS header card."""
    if len(keyword) > 8:
        raise ValueError(f"Keyword {keyword!r} exceeds 8 characters")

    for field_name, text in (("keyword", keyword), ("comment", comment)):
        try:
            text.encode("ascii")
        except UnicodeEncodeError:
            msg = f"FITS card {field_name} contains non-ASCII characters: {text!r}"
            raise ValueError(msg) from None

    if isinstance(value, str):
        try:
            value.encode("ascii")
        except UnicodeEncodeError:
            raise ValueError(
                f"FITS card string value contains non-ASCII characters: {value!r}"
            ) from None

    kw_field = keyword.ljust(8, " ")

    if isinstance(value, bool):
        val_str = ("T" if value else "F").rjust(20, " ")
        card = f"{kw_field}= {val_str}"
    elif isinstance(value, (int, np.integer)):
        val_str = str(int(value)).rjust(20, " ")
        card = f"{kw_field}= {val_str}"
    elif isinstance(value, (float, np.floating)):
        f_val = float(value)
        s_val = str(f_val)
        if len(s_val) > 20:
            s_val = f"{f_val:.14g}"
            if len(s_val) > 20:
                s_val = f"{f_val:.12e}"
        val_str = s_val.rjust(20, " ")
        card = f"{kw_field}= {val_str}"
    elif isinstance(value, str):
        inside_quotes = value.ljust(8, " ")
        card = f"{kw_field}= '{inside_quotes}'"
    else:
        raise ValueError(
            f"Unsupported value type for keyword {keyword!r}: {type(value).__name__}; "
            f"expected bool, int, float, or str"
        )

    if comment:
        card += f" / {comment}"

    return card.ljust(_CARD_SIZE, " ")[:_CARD_SIZE]


def pack_fits(
    data: np.ndarray,
    cards: Sequence[tuple[str, object, str]] | None = None,
) -> bytes:
    """Pack a 2-D numpy array and optional header cards into a single FITS primary HDU.

    data must be a 2-D array of dtype uint8, uint16, or float32.
    Header is padded with spaces and data is padded with zero bytes, each to a multiple
    of 2880 bytes. Data is stored in big-endian byte order.
    """
    if data.ndim != 2:
        raise ValueError(f"data must be a 2-D array, got shape {data.shape} ({data.ndim}-D)")

    dtype = data.dtype
    if dtype not in (np.dtype("uint8"), np.dtype("uint16"), np.dtype("float32")):
        raise ValueError(
            f"data dtype must be uint8, uint16, or float32, got {dtype}"
        )

    rows, cols = data.shape
    naxis1 = cols  # columns (width)
    naxis2 = rows  # rows (height)

    # Determine BITPIX, BZERO, and binary representation
    card_tuples: list[tuple[str, object, str]] = []
    if dtype == np.dtype("uint8"):
        bitpix = 8
        card_tuples.extend([
            ("SIMPLE", True, ""),
            ("BITPIX", bitpix, ""),
            ("NAXIS", 2, ""),
            ("NAXIS1", naxis1, ""),
            ("NAXIS2", naxis2, ""),
        ])
        data_bytes = data.astype("u1").tobytes()
    elif dtype == np.dtype("uint16"):
        bitpix = 16
        card_tuples.extend([
            ("SIMPLE", True, ""),
            ("BITPIX", bitpix, ""),
            ("NAXIS", 2, ""),
            ("NAXIS1", naxis1, ""),
            ("NAXIS2", naxis2, ""),
            ("BZERO", 32768, ""),
            ("BSCALE", 1, ""),
        ])
        data_bytes = (data.astype(np.int32) - 32768).astype(">i2").tobytes()
    else:  # float32
        bitpix = -32
        card_tuples.extend([
            ("SIMPLE", True, ""),
            ("BITPIX", bitpix, ""),
            ("NAXIS", 2, ""),
            ("NAXIS1", naxis1, ""),
            ("NAXIS2", naxis2, ""),
        ])
        data_bytes = data.astype(">f4").tobytes()

    if cards:
        card_tuples.extend(cards)

    # Build formatted header cards
    card_lines = [_format_card(kw, val, cmt) for kw, val, cmt in card_tuples]
    card_lines.append("END".ljust(_CARD_SIZE, " "))

    header_text = "".join(card_lines)
    header_bytes = header_text.encode("ascii")

    # Pad header to multiple of 2880 with ASCII spaces
    hdr_rem = len(header_bytes) % _BLOCK_SIZE
    if hdr_rem != 0:
        header_bytes += b" " * (_BLOCK_SIZE - hdr_rem)

    # Pad data to multiple of 2880 with zero bytes
    data_rem = len(data_bytes) % _BLOCK_SIZE
    if data_rem != 0:
        data_bytes += b"\x00" * (_BLOCK_SIZE - data_rem)

    return header_bytes + data_bytes
