"""
Tests for Faraday cup status decoding from raw FIO_STATE integers.

ADR 0003 & Ticket 02:
Status decoding is a pure function in rbl/hardware/ with no Qt or LabJack imports.
A closed status contact pulls the FIO line to ground (bit value 0).
Closed is zero. The decoding function inverts.
"""
import sys

from rbl.config.cup_config import (
    CUP_ARM_DEBOUNCE_S,
    CUP_COMMAND_OUT_LINE,
    CUP_CONTACT_DEBOUNCE_S,
    CUP_ENABLE_LINE,
    CUP_MOVE_CONFIRMATION_TIMEOUT_S,
    CUP_RELEASE_INTERVAL_S,
    CUP_STATUS_BIT_AUTO,
    CUP_STATUS_BIT_IN,
    CUP_STATUS_BIT_OUT,
)
from rbl.hardware.cup_status import CupPosition, CupStatus, decode_cup_status


class TestCupStatusConfig:
    """Verify ticket 02 config additions and preservation of inference constants."""

    def test_line_names_and_bit_positions(self):
        assert CUP_ENABLE_LINE == "FIO0"
        assert CUP_COMMAND_OUT_LINE == "FIO1"
        assert CUP_STATUS_BIT_IN == 2
        assert CUP_STATUS_BIT_OUT == 3
        assert CUP_STATUS_BIT_AUTO == 4

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
        in_contact_closed: bool,
        out_contact_closed: bool,
        auto_closed: bool,
    ) -> int:
        """Helper building an FIO_STATE word where closed == 0 and open == 1."""
        word = 0xFFFF
        if in_contact_closed:
            word &= ~(1 << CUP_STATUS_BIT_IN)
        else:
            word |= (1 << CUP_STATUS_BIT_IN)

        if out_contact_closed:
            word &= ~(1 << CUP_STATUS_BIT_OUT)
        else:
            word |= (1 << CUP_STATUS_BIT_OUT)

        if auto_closed:
            word &= ~(1 << CUP_STATUS_BIT_AUTO)
        else:
            word |= (1 << CUP_STATUS_BIT_AUTO)
        return word

    def test_decode_cup_in(self):
        # IN contact closed (0), OUT open (1), AUTO closed (0)
        word = self._make_word(
            in_contact_closed=True,
            out_contact_closed=False,
            auto_closed=True,
        )
        status = decode_cup_status(word)
        assert isinstance(status, CupStatus)
        assert status.position == CupPosition.IN
        assert status.auto_mode is True

    def test_decode_cup_out(self):
        # IN open (1), OUT closed (0), AUTO closed (0)
        word = self._make_word(
            in_contact_closed=False,
            out_contact_closed=True,
            auto_closed=True,
        )
        status = decode_cup_status(word)
        assert status.position == CupPosition.OUT
        assert status.auto_mode is True

    def test_decode_in_transit_neither_asserted(self):
        # IN open (1), OUT open (1), AUTO closed (0)
        word = self._make_word(
            in_contact_closed=False,
            out_contact_closed=False,
            auto_closed=True,
        )
        status = decode_cup_status(word)
        assert status.position == CupPosition.IN_TRANSIT
        assert status.auto_mode is True

    def test_decode_indeterminate_both_asserted(self):
        # IN closed (0), OUT closed (0), AUTO closed (0)
        word = self._make_word(
            in_contact_closed=True,
            out_contact_closed=True,
            auto_closed=True,
        )
        status = decode_cup_status(word)
        assert status.position == CupPosition.INDETERMINATE
        assert status.auto_mode is True

    def test_decode_auto_asserted_and_not_asserted(self):
        # AUTO asserted (contact closed -> bit 4 is 0)
        word_auto = self._make_word(
            in_contact_closed=True,
            out_contact_closed=False,
            auto_closed=True,
        )
        assert decode_cup_status(word_auto).auto_mode is True

        # AUTO not asserted (contact open -> bit 4 is 1)
        word_manual = self._make_word(
            in_contact_closed=True,
            out_contact_closed=False,
            auto_closed=False,
        )
        assert decode_cup_status(word_manual).auto_mode is False

    def test_explicit_closed_contact_is_zero_inversion(self):
        """Explicitly test the polarity rule: closed contact pulls to GND (bit value 0)."""
        # FIO2=0, FIO3=1, FIO4=0 -> IN=True, OUT=False, AUTO=True
        raw_int = (1 << CUP_STATUS_BIT_OUT)  # Only OUT bit is 1; IN and AUTO bits are 0
        status = decode_cup_status(raw_int)
        assert status.position == CupPosition.IN
        assert status.auto_mode is True

        # FIO2=1, FIO3=0, FIO4=1 -> IN=False, OUT=True, AUTO=False
        raw_int2 = (1 << CUP_STATUS_BIT_IN) | (1 << CUP_STATUS_BIT_AUTO)  # IN and AUTO bits are 1
        status2 = decode_cup_status(raw_int2)
        assert status2.position == CupPosition.OUT
        assert status2.auto_mode is False

    def test_all_ones_word_returns_in_transit_and_auto_false(self):
        """An all-ones word (all contacts open) must decode as IN_TRANSIT and auto_mode False."""
        all_ones = 0xFFFF
        status = decode_cup_status(all_ones)
        assert status.position == CupPosition.IN_TRANSIT
        assert status.auto_mode is False
        assert status.position != CupPosition.IN
        assert status.position != CupPosition.OUT

    def test_unpacking_as_tuple(self):
        """CupStatus namedtuple can be unpacked into (position, auto_mode)."""
        word = self._make_word(
            in_contact_closed=True,
            out_contact_closed=False,
            auto_closed=True,
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

