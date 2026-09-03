"""
snapshots.py  (rbl/state/snapshots.py — backward-compatibility re-export)

The snapshot types have moved to rbl.snapshots (package root) so that hardware
workers can import them without creating a hardware → state layering violation.

This stub re-exports everything so existing importers of
    from rbl.state.snapshots import Foo
continue to work without any source change.  New code should import directly
from rbl.snapshots.
"""
from rbl.snapshots import (  # noqa: F401 — re-export
    AxisSnapshot,
    MotorState,
    ChannelSnapshot,
    ChannelParams,
    FuncGenState,
    LogAmpState,
    AmpChannelSnapshot,
    VacuumState,
    ScopeState,
    AmpState,
)
