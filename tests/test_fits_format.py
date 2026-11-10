"""
test_fits_format.py
Contract tests for FITS primary HDU byte format packaging.

Tests parse the bytes using independent test-side readers written from the
FITS specification, never using fits_format itself.
"""
import ast
import sys
from pathlib import Path

import numpy as np
import pytest

from rbl.hardware.fits_format import pack_fits


def _parse_fits_header_cards(header_bytes: bytes) -> list[str]:
    """Parse header bytes into 80-character card strings until END card."""
    assert len(header_bytes) % 2880 == 0
    cards: list[str] = []
    for i in range(0, len(header_bytes), 80):
        card = header_bytes[i : i + 80].decode("ascii")
        cards.append(card)
        if card.startswith("END"):
            break
    return cards


def test_uint16_round_trip_and_layout() -> None:
    """A 3x5 uint16 array containing 0 and 65535 is packed with proper FITS layout."""
    arr = np.array(
        [
            [0, 100, 32768, 50000, 65535],
            [1, 2, 3, 4, 5],
            [10, 20, 30, 40, 50],
        ],
        dtype=np.uint16,
    )
    assert arr.shape == (3, 5)

    raw_fits = pack_fits(arr)

    # Total length is a positive multiple of 2880
    assert len(raw_fits) > 0
    assert len(raw_fits) % 2880 == 0

    # Header is at least one 2880 block
    header_block = raw_fits[:2880]
    first_card = header_block[:80].decode("ascii")
    expected_simple = "SIMPLE  =                    T".ljust(80, " ")
    assert first_card == expected_simple

    cards = _parse_fits_header_cards(header_block)
    card_keywords = [c[:8].strip() for c in cards]

    # Mandatory cards in exact order
    assert card_keywords[:5] == ["SIMPLE", "BITPIX", "NAXIS", "NAXIS1", "NAXIS2"]

    # NAXIS1 is columns (5), NAXIS2 is rows (3)
    assert int(cards[3][10:30]) == 5
    assert int(cards[4][10:30]) == 3

    # uint16 BITPIX is 16, followed by BZERO = 32768 and BSCALE = 1
    assert int(cards[1][10:30]) == 16
    assert card_keywords[5] == "BZERO"
    assert int(cards[5][10:30]) == 32768
    assert card_keywords[6] == "BSCALE"
    assert int(cards[6][10:30]) == 1

    # END card is present
    assert "END" in card_keywords
    end_index = card_keywords.index("END")
    end_card_offset = end_index * 80

    # Everything after END in the header block is spaces
    after_end = header_block[end_card_offset + 80 :]
    assert after_end == b" " * len(after_end)

    # Data starts after the 2880-byte header block
    data_block = raw_fits[2880:]
    data_bytes_len = 3 * 5 * 2  # 30 bytes
    data_payload = data_block[:data_bytes_len]
    padding_payload = data_block[data_bytes_len:]

    # Data padding bytes are zero
    assert padding_payload == b"\x00" * len(padding_payload)

    # Test-side reader: decode as big-endian signed int16 (>i2) plus BZERO (32768)
    recovered_int16 = np.frombuffer(data_payload, dtype=">i2").reshape((3, 5))
    recovered_uint16 = (recovered_int16.astype(np.int64) + 32768).astype(np.uint16)

    np.testing.assert_array_equal(recovered_uint16, arr)
    assert recovered_uint16[0, 0] == 0
    assert recovered_uint16[0, 4] == 65535


def test_uint8_float32_and_cards() -> None:
    """uint8 and float32 encodings, caller card formatting, and validation checks."""
    # 1. uint8 array
    arr_u8 = np.arange(24, dtype=np.uint8).reshape((4, 6))
    fits_u8 = pack_fits(arr_u8)
    assert len(fits_u8) % 2880 == 0
    cards_u8 = _parse_fits_header_cards(fits_u8[:2880])
    kw_u8 = [c[:8].strip() for c in cards_u8]
    assert int(cards_u8[1][10:30]) == 8  # BITPIX = 8
    assert "BZERO" not in kw_u8

    # Recover uint8 data
    recovered_u8 = np.frombuffer(fits_u8[2880 : 2880 + 24], dtype="u1").reshape((4, 6))
    np.testing.assert_array_equal(recovered_u8, arr_u8)

    # 2. float32 array
    arr_f32 = np.array([[1.5, -2.5, 0.0, 100.25], [-0.5, 3.14, 2.718, 42.0]], dtype=np.float32)
    fits_f32 = pack_fits(arr_f32)
    assert len(fits_f32) % 2880 == 0
    cards_f32 = _parse_fits_header_cards(fits_f32[:2880])
    assert int(cards_f32[1][10:30]) == -32  # BITPIX = -32

    # Recover float32 data
    f32_slice = fits_f32[2880 : 2880 + arr_f32.nbytes]
    recovered_f32 = np.frombuffer(f32_slice, dtype=">f4").reshape((2, 4))
    np.testing.assert_allclose(recovered_f32, arr_f32)

    # 3. Caller cards
    caller_cards: list[tuple[str, object, str]] = [
        ("EXPTIME", 0.02, "s"),
        ("GAIN", 100, ""),
        ("BAYERPAT", "RG", ""),
        ("SIMPLE2", True, ""),
    ]
    fits_with_cards = pack_fits(arr_u8, caller_cards)
    cards_all = _parse_fits_header_cards(fits_with_cards[:2880])

    card_map = {c[:8].strip(): c for c in cards_all}

    # EXPTIME: float right-justified ending in column 30, followed by comment
    card_exp = card_map["EXPTIME"]
    assert card_exp[8:10] == "= "
    assert card_exp[10:30].strip() == "0.02"
    assert " / s" in card_exp

    # GAIN: integer ending in column 30 (index 29 is the last digit)
    card_gain = card_map["GAIN"]
    assert card_gain[8:10] == "= "
    assert card_gain[29] == "0"
    assert card_gain[10:30].strip() == "100"

    # BAYERPAT: string value starting in column 11 (index 10) inside quotes
    card_bayer = card_map["BAYERPAT"]
    assert card_bayer[8:10] == "= "
    assert card_bayer[10] == "'"
    assert card_bayer[11:13] == "RG"
    assert card_bayer[10:20] == "'RG      '"

    # SIMPLE2: bool right-justified ending in column 30
    card_s2 = card_map["SIMPLE2"]
    assert card_s2[8:10] == "= "
    assert card_s2[29] == "T"

    # Validation: keyword > 8 chars
    with pytest.raises(ValueError, match="exceeds 8 characters"):
        pack_fits(arr_u8, [("LONGKEYWORD", 1, "")])

    # Validation: non-ASCII string in keyword, value, or comment
    with pytest.raises(ValueError, match="non-ASCII"):
        pack_fits(arr_u8, [("KÉY", 1, "")])
    with pytest.raises(ValueError, match="non-ASCII"):
        pack_fits(arr_u8, [("KEY", "résumé", "")])
    with pytest.raises(ValueError, match="non-ASCII"):
        pack_fits(arr_u8, [("KEY", 1, "café")])

    # Validation: list value
    with pytest.raises(ValueError, match="Unsupported value type"):
        pack_fits(arr_u8, [("KEY", [1, 2], "")])

    # Validation: 3-D array
    with pytest.raises(ValueError, match="2-D"):
        pack_fits(np.zeros((2, 2, 2), dtype=np.uint8))

    # Validation: int32 array
    with pytest.raises(ValueError, match="dtype"):
        pack_fits(np.zeros((2, 2), dtype=np.int32))


def test_imports_are_stdlib_and_numpy_only() -> None:
    """rbl.hardware.fits_format imports only standard library and numpy."""
    mod_path = (
        Path(__file__).resolve().parent.parent / "src" / "rbl" / "hardware" / "fits_format.py"
    )
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
