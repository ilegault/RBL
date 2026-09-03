"""
labjack_consumer.py
Protocol (structural interface) for tabs that consume LabJack T7 events.

WHY THIS EXISTS
---------------
MainWindow fans out five LabJack lifecycle signals to a tuple of five tabs
(_lj_tabs). Before this file, those tabs shared no declared interface: the
call site used duck-typing with a `hasattr` guard for `on_profile_changed`,
and `_on_error` was a private method that app.py reached into directly.

Declaring the protocol here makes the expected interface explicit and answers
"what must a tab implement to sit in `_lj_tabs`?" without reading app.py.
All four methods have no-op defaults so existing tabs that don't need one
method (e.g. a tab that doesn't react to profile changes) need not add it.
"""
from typing import Protocol, runtime_checkable


@runtime_checkable
class LabJackConsumer(Protocol):
    """Structural interface for tabs that consume LabJack T7 lifecycle events.

    Implement all four to be placed in MainWindow._lj_tabs.  Methods may be
    no-ops; the protocol just makes the contract visible.
    """

    def on_labjack_connected(self, serial: str) -> None:
        """Called after the T7 opens successfully.  `serial` is the device serial."""
        ...

    def on_labjack_disconnected(self) -> None:
        """Called after the T7 stream is stopped and the handle closed."""
        ...

    def on_labjack_error(self, msg: str) -> None:
        """Called when the stream worker reports a fatal error.  The connection
        has already been torn down by the time this arrives."""
        ...

    def on_profile_changed(self, profile_name: str) -> None:
        """Called when the active acquisition profile changes (e.g. switching
        from WAVEFORM to AMP_PAIR mid-session).  No-op is acceptable."""
        ...
