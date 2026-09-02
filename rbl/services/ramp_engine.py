"""
ramp_engine.py  (rbl/services/ramp_engine.py — backward-compatibility re-export)

RampEngine has moved to rbl.hardware.ramp_engine.  It is a slew-limiter with
no I/O of its own — policy, not orchestration — so it belongs in the hardware
layer where state can import it without creating a state → services violation.

This stub re-exports RampEngine so existing importers continue to work.
New code should import from rbl.hardware.ramp_engine directly.
"""
from rbl.hardware.ramp_engine import RampEngine  # noqa: F401 — re-export
