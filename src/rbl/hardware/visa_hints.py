"""
visa_hints.py
Plain-language operator hints for the VISA status names the GPIB chain produces.

WHY THIS EXISTS
---------------
On 2026-11-10 the lab PC showed ``VI_ERROR_LIBRARY_NFOUND (-1073807202)`` when
connecting the Keithley 6482. That status name does not tell an operator that the
likely fix is installing Keysight IO Libraries Suite on that PC. Each of the four
errors this chain has actually produced has a known operator action, so the worker
appends a one-sentence hint to its error string. The hint rides inside the existing
``error`` signal, so the log, ``Beamline.cup_error`` and the Faraday Cup tab all carry
it with no other change.

This is a pure function with no Qt, no pyvisa and no app state, so it is tested with
literal messages copied from real output and never patched.
"""
from __future__ import annotations

# Order matters: the first substring found in the message wins.
_HINTS: tuple[tuple[str, str], ...] = (
    (
        "VI_ERROR_LIBRARY_NFOUND",
        "Hint: this PC's VISA could not load its GPIB library. Check that Keysight IO "
        "Libraries Suite is installed on this PC and the 82357B adapter is plugged into "
        "it, then rescan in Keysight Connection Expert.",
    ),
    (
        "VI_ERROR_RSRC_NFOUND",
        "Hint: nothing answered at this address. Rescan in Keysight Connection Expert "
        "and check the address on the 6482 front panel (MENU > COMMUNICATION > GPIB).",
    ),
    (
        "VI_ERROR_TMO",
        "Hint: the instrument did not answer in time. Check the 6482 is powered, set to "
        "GPIB (not RS-232), not inside a front-panel menu, and set to this address.",
    ),
    (
        "VI_ERROR_RSRC_BUSY",
        "Hint: another program is holding this instrument. Close Keysight Connection "
        "Expert, NI MAX and any other copy of RBL.",
    ),
)


def visa_error_hint(message: str) -> str | None:
    """Return the operator hint for the first known VISA status in ``message``, else None."""
    for status, hint in _HINTS:
        if status in message:
            return hint
    return None
