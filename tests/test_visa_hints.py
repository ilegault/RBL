"""Tests for the pure VISA-status -> operator-hint and short-reason lookups."""
from rbl.hardware.visa_hints import visa_error_hint, visa_short_reason


def test_each_known_visa_error_gets_its_hint():
    cases = [
        (
            "Failed to connect to Keithley 6482 on GPIB0::14::INSTR: VI_ERROR_LIBRARY_NFOUND "
            "(-1073807202): A code library required by VISA could not be located or loaded.",
            "Keysight IO Libraries Suite",
        ),
        (
            "VI_ERROR_RSRC_NFOUND (-1073807343): Insufficient location information or the "
            "requested device or resource is not present in the system.",
            "MENU > COMMUNICATION > GPIB",
        ),
        (
            "VI_ERROR_TMO (-1073807339): Timeout expired before operation completed.",
            "not inside a front-panel menu",
        ),
        (
            "VI_ERROR_RSRC_BUSY (-1073807246): The resource is valid, but VISA cannot "
            "currently access it.",
            "Close Keysight Connection Expert",
        ),
    ]
    for message, expected in cases:
        hint = visa_error_hint(message)
        assert hint is not None and hint.startswith("Hint: ")
        assert expected in hint


def test_unknown_error_has_no_hint():
    assert visa_error_hint("Keithley 6482 read error: could not convert string to float") is None
    assert visa_error_hint("") is None


def test_short_reason_for_each_known_visa_error():
    cases = [
        (
            "Failed to connect to Keithley 6482 on GPIB0::14::INSTR: VI_ERROR_LIBRARY_NFOUND "
            "(-1073807202): A code library required by VISA could not be located or loaded.",
            "GPIB library not found on this PC",
        ),
        (
            "VI_ERROR_RSRC_NFOUND (-1073807343): Insufficient location information or the "
            "requested device or resource is not present in the system.",
            "nothing answered at this GPIB address",
        ),
        (
            "VI_ERROR_TMO (-1073807339): Timeout expired before operation completed.",
            "instrument did not answer in time",
        ),
        (
            "VI_ERROR_RSRC_BUSY (-1073807246): The resource is valid, but VISA cannot "
            "currently access it.",
            "another program is holding the instrument",
        ),
    ]
    for message, expected in cases:
        assert visa_short_reason(message) == expected


def test_short_reason_for_unknown_error_is_first_line_clipped():
    assert visa_short_reason("short failure\nsecond line") == "short failure"
    clipped = visa_short_reason("x" * 120)
    assert clipped == ("x" * 79) + "\u2026"
    assert len(clipped) == 80
