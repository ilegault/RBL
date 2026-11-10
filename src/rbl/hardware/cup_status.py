"""
cup_status.py
Pure status decoding for the Faraday Cup Controller position and AUTO contacts.

WHY THIS EXISTS
---------------
The Faraday Cup Controller reports position and mode via three isolated dry
contacts to ground on the CB37 terminal board (FIO2 = IN, FIO3 = OUT, FIO4 = AUTO).
A status line reads 1 when its condition is asserted (bench, 2026-11-10, cup IN in
AUTO: FIO2 = 1, FIO3 = 0, FIO4 = 1); the level is CUP_STATUS_ASSERTED_LEVEL. A
disconnected status cable floats all three lines high on the T7's pull-ups and so
reads INDETERMINATE with AUTO asserted.

This module provides a pure decoding function over raw FIO_STATE integers. It has
no hardware or Qt dependencies, making the decode logic verifiable without any
hardware attached.

TRANSITIONAL AND IMPOSSIBLE STATES
----------------------------------
- IN_TRANSIT: Neither IN nor OUT contact asserted (both lines read 0). This is
  the expected normal state while the cup is travelling between IN and OUT.
- INDETERMINATE: Both IN and OUT contacts asserted simultaneously (both lines read 1).
  This indicates a controller or wiring fault. It is never resolved in favour of
  one contact over the other.
"""
from enum import Enum
from typing import NamedTuple

from rbl.config.cup_config import (
    CUP_STATUS_ASSERTED_LEVEL,
    CUP_STATUS_BIT_AUTO,
    CUP_STATUS_BIT_IN,
    CUP_STATUS_BIT_OUT,
)


class CupPosition(str, Enum):
    """Confirmed physical position of the Faraday cup.

    IN: Cup is fully inserted into the beam path (intercepting beam).
    OUT: Cup is fully retracted out of the beam path.
    IN_TRANSIT: Cup is in motion between positions (neither contact asserted).
    INDETERMINATE: Both contacts asserted simultaneously (wiring or controller fault).
    """

    IN = "IN"
    OUT = "OUT"
    IN_TRANSIT = "IN_TRANSIT"
    INDETERMINATE = "INDETERMINATE"


class CupStatus(NamedTuple):
    """Decoded Faraday cup status.

    position: Confirmed physical position of the cup.
    auto_mode: True if the controller is in AUTO mode (remote commands honoured).
    """

    position: CupPosition
    auto_mode: bool


def decode_cup_status(fio_state: int) -> CupStatus:
    """Decode raw FIO_STATE integer into confirmed position and AUTO mode flag.

    A status line reads 1 when its condition is asserted (bench, 2026-11-10, cup IN
    in AUTO: FIO2 = 1, FIO3 = 0, FIO4 = 1); the level is CUP_STATUS_ASSERTED_LEVEL.
    A disconnected status cable floats all three lines high on the T7's pull-ups and
    so reads INDETERMINATE with AUTO asserted.

    States:
    - in_asserted only: CupPosition.IN
    - out_asserted only: CupPosition.OUT
    - neither asserted: CupPosition.IN_TRANSIT (normal moving state)
    - both asserted: CupPosition.INDETERMINATE (fault; never resolved in favour of either)
    - auto_asserted: auto_mode is True (controller honours remote actuation)
    """
    in_asserted = ((fio_state >> CUP_STATUS_BIT_IN) & 1) == CUP_STATUS_ASSERTED_LEVEL
    out_asserted = ((fio_state >> CUP_STATUS_BIT_OUT) & 1) == CUP_STATUS_ASSERTED_LEVEL
    auto_asserted = ((fio_state >> CUP_STATUS_BIT_AUTO) & 1) == CUP_STATUS_ASSERTED_LEVEL

    if in_asserted and out_asserted:
        position = CupPosition.INDETERMINATE
    elif in_asserted:
        position = CupPosition.IN
    elif out_asserted:
        position = CupPosition.OUT
    else:
        position = CupPosition.IN_TRANSIT

    return CupStatus(position=position, auto_mode=auto_asserted)
