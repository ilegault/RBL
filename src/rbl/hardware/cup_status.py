"""
cup_status.py
Pure status decoding for the Faraday Cup Controller position and AUTO contacts.

WHY THIS EXISTS
---------------
The Faraday Cup Controller reports position and mode via three isolated dry
contacts to ground on the CB37 terminal board (FIO2 = IN, FIO3 = OUT, FIO4 = AUTO).
Because these contacts pull the lines to ground when closed, a closed contact
reads as bit value 0 in FIO_STATE.

A status contact that is closed pulls its FIO line to ground and therefore reads as
bit value 0. Closed is zero. The decoding function inverts.

This module provides a pure decoding function over raw FIO_STATE integers. It has
no hardware or Qt dependencies, making the decode logic verifiable without any
hardware attached.

TRANSITIONAL AND IMPOSSIBLE STATES
----------------------------------
- IN_TRANSIT: Neither IN nor OUT contact asserted (both lines read 1). This is
  the expected normal state while the cup is travelling between IN and OUT.
- INDETERMINATE: Both IN and OUT contacts asserted simultaneously (both lines read 0).
  This indicates a controller or wiring fault. It is never resolved in favour of
  one contact over the other.
"""
from enum import Enum
from typing import NamedTuple

from rbl.config.cup_config import (
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

    A status contact that is closed pulls its FIO line to ground and therefore reads as
    bit value 0. Closed is zero. The decoding function inverts.

    States:
    - in_closed only: CupPosition.IN
    - out_closed only: CupPosition.OUT
    - neither closed: CupPosition.IN_TRANSIT (normal moving state)
    - both closed: CupPosition.INDETERMINATE (fault; never resolved in favour of either)
    - auto_closed: auto_mode is True (controller honours remote actuation)
    """
    in_closed = ((fio_state >> CUP_STATUS_BIT_IN) & 1) == 0
    out_closed = ((fio_state >> CUP_STATUS_BIT_OUT) & 1) == 0
    auto_closed = ((fio_state >> CUP_STATUS_BIT_AUTO) & 1) == 0

    if in_closed and out_closed:
        position = CupPosition.INDETERMINATE
    elif in_closed:
        position = CupPosition.IN
    elif out_closed:
        position = CupPosition.OUT
    else:
        position = CupPosition.IN_TRANSIT

    return CupStatus(position=position, auto_mode=auto_closed)
