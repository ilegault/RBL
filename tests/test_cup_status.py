"""
Tests for Faraday cup status decoding from raw FIO_STATE integers.

ADR 0003 & Ticket 02 & Ticket 167:
Status decoding is a pure function in rbl/hardware/ with no Qt or LabJack imports.
A status line reads 0 when its condition is asserted (contacts close to GND; meter
2026-11-08 and 2026-11-20); FIO3 carries IN, FIO2 carries OUT, FIO4 carries AUTO.
"""
import sys

import rbl.config.cup_config
from rbl.config.cup_config import (
    CUP_ARM_DEBOUNCE_S,
    CUP_COMMAND_OUT_LINE,
    CUP_CONTACT_DEBOUNCE_S,
    CUP_INTERLOCK_LINE,
    CUP_MOVE_CONFIRMATION_TIMEOUT_S,
    CUP_RELEASE_INTERVAL_S,
    CUP_STATUS_ASSERTED_LEVEL,
    CUP_STATUS_BIT_AUTO,
    CUP_STATUS_BIT_IN,
    CUP_STATUS_BIT_OUT,
)
from rbl.hardware.cup_status import CupPosition, CupStatus, decode_cup_status


class TestCupStatusConfig:
    """Verify ticket 02 config additions and preservation of inference constants."""

    def test_line_names_and_bit_positions(self):
        assert CUP_INTERLOCK_LINE == "FIO0"
        assert CUP_COMMAND_OUT_LINE == "FIO1"
        assert CUP_STATUS_BIT_IN == 3
        assert CUP_STATUS_BIT_OUT == 2
        assert CUP_STATUS_BIT_AUTO == 4
        assert CUP_STATUS_ASSERTED_LEVEL == 0
        assert not hasattr(rbl.config.cup_config, "CUP_ENABLE_LINE")
        assert not hasattr(rbl.config.cup_config, "CUP_ENABLE_OUT_LINE")

    def test_timing_constants(self):
        assert CUP_MOVE_CONFIRMATION_TIMEOUT_S == 2.0
        assert CUP_CONTACT_DEBOUNCE_S == 0.05

    def test_inference_constants_untouched(self):
        """Inference constants for hand insertions must not be modified."""
        assert CUP_ARM_DEBOUNCE_S == 1.0
        assert CUP_RELEASE_INTERVAL_S == 3.0


class TestCupStatusPureDecodes:
    """Verify decode_cup_status over raw integers across all contact permutations."""

    def _make_word(
        self,
        in_asserted: bool,
        out_asserted: bool,
        auto_asserted: bool,
    ) -> int:
        """Helper building an FIO_STATE word where asserted == 0 and non-asserted == 1."""
        word = 0b11100
        if in_asserted:
            word &= ~(1 << CUP_STATUS_BIT_IN)
        if out_asserted:
            word &= ~(1 << CUP_STATUS_BIT_OUT)
        if auto_asserted:
            word &= ~(1 << CUP_STATUS_BIT_AUTO)
        return word

    def test_decode_cup_in(self):
        # IN asserted (1), OUT not asserted (0), AUTO asserted (1)
        word = self._make_word(
            in_asserted=True,
            out_asserted=False,
            auto_asserted=True,
        )
        status = decode_cup_status(word)
        assert isinstance(status, CupStatus)
        assert status.position == CupPosition.IN
        assert status.auto_mode is True

    def test_decode_cup_out(self):
        # IN not asserted (0), OUT asserted (1), AUTO asserted (1)
        word = self._make_word(
            in_asserted=False,
            out_asserted=True,
            auto_asserted=True,
        )
        status = decode_cup_status(word)
        assert status.position == CupPosition.OUT
        assert status.auto_mode is True

    def test_decode_in_transit_neither_asserted(self):
        # IN not asserted (0), OUT not asserted (0), AUTO asserted (1)
        word = self._make_word(
            in_asserted=False,
            out_asserted=False,
            auto_asserted=True,
        )
        status = decode_cup_status(word)
        assert status.position == CupPosition.IN_TRANSIT
        assert status.auto_mode is True

    def test_decode_indeterminate_both_asserted(self):
        # IN asserted (1), OUT asserted (1), AUTO asserted (1)
        word = self._make_word(
            in_asserted=True,
            out_asserted=True,
            auto_asserted=True,
        )
        status = decode_cup_status(word)
        assert status.position == CupPosition.INDETERMINATE
        assert status.auto_mode is True

    def test_decode_auto_asserted_and_not_asserted(self):
        # AUTO asserted (bit 4 is 1)
        word_auto = self._make_word(
            in_asserted=True,
            out_asserted=False,
            auto_asserted=True,
        )
        assert decode_cup_status(word_auto).auto_mode is True

        # AUTO not asserted (bit 4 is 0)
        word_manual = self._make_word(
            in_asserted=True,
            out_asserted=False,
            auto_asserted=False,
        )
        assert decode_cup_status(word_manual).auto_mode is False

    def test_asserted_level_constant_controls_the_decode(self, monkeypatch):
        """Monkeypatching CUP_STATUS_ASSERTED_LEVEL to 1 inverts the decode."""
        monkeypatch.setattr("rbl.hardware.cup_status.CUP_STATUS_ASSERTED_LEVEL", 1)
        status = decode_cup_status(4)
        assert status.position == CupPosition.OUT
        assert status.auto_mode is False

    def test_bench_word_cup_in_auto_decodes_in_and_auto(self):
        """Bench word 4 (0b00100: FIO4=0, FIO3=0, FIO2=1; meter 2026-11-20) -> IN, AUTO."""
        status = decode_cup_status(4)
        assert status == CupStatus(CupPosition.IN, True)

    def test_hardware_words_decode_per_line(self):
        """Hardware words decode correctly across individual lines and combinations."""
        cases = [
            (8, CupPosition.OUT, True),
            (12, CupPosition.IN_TRANSIT, True),
            (0, CupPosition.INDETERMINATE, True),
            (20, CupPosition.IN, False),
            (28, CupPosition.IN_TRANSIT, False),
            (0xFFE3, CupPosition.INDETERMINATE, True),
        ]
        for word, expected_pos, expected_auto in cases:
            status = decode_cup_status(word)
            assert status.position == expected_pos, (
                f"Failed position for word {word} (0b{word:b})"
            )
            assert status.auto_mode is expected_auto, (
                f"Failed auto_mode for word {word} (0b{word:b})"
            )

    def test_status_lines_read_zero_when_asserted(self):
        """Integer literals verify 0-when-asserted with FIO3=IN, FIO2=OUT, FIO4=AUTO."""
        assert decode_cup_status(4) == CupStatus(CupPosition.IN, True)
        assert decode_cup_status(8) == CupStatus(CupPosition.OUT, True)
        assert decode_cup_status(12) == CupStatus(CupPosition.IN_TRANSIT, True)
        assert decode_cup_status(20) == CupStatus(CupPosition.IN, False)

    def test_unplugged_status_cable_reads_in_transit_and_not_auto(self):
        """All pull-ups high (cable unplugged) reads IN_TRANSIT and auto_mode False."""
        assert decode_cup_status(28) == CupStatus(CupPosition.IN_TRANSIT, False)
        assert decode_cup_status(0xFFFF) == CupStatus(CupPosition.IN_TRANSIT, False)

    def test_both_contacts_closed_reads_indeterminate(self):
        """Both IN and OUT closed to GND (0) reads INDETERMINATE regardless of AUTO."""
        assert decode_cup_status(0) == CupStatus(CupPosition.INDETERMINATE, True)
        assert decode_cup_status(16) == CupStatus(CupPosition.INDETERMINATE, False)

    def test_unpacking_as_tuple(self):
        """CupStatus namedtuple can be unpacked into (position, auto_mode)."""
        word = self._make_word(
            in_asserted=True,
            out_asserted=False,
            auto_asserted=True,
        )
        pos, auto = decode_cup_status(word)
        assert pos == CupPosition.IN
        assert auto is True


class TestNoForbiddenImports:
    """rbl.hardware.cup_status must be pure: no Qt, no labjack."""

    def test_imports_clean(self):
        import rbl.hardware.cup_status as cs
        mod_name = cs.__name__
        has_qt = any(
            k.startswith("PySide6")
            for k, m in sys.modules.items()
            if getattr(m, "__file__", None) and mod_name in str(getattr(m, "__file__", ""))
        )
        assert not has_qt
        for obj in cs.__dict__.values():
            mod = getattr(obj, "__module__", "")
            assert not mod.startswith("PySide6"), f"Found Qt import in cup_status: {obj}"
            assert not mod.startswith("labjack"), f"Found labjack import in cup_status: {obj}"

