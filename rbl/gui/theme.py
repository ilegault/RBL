"""
theme.py
Shared colour roles and stylesheet helpers for the GUI tabs.

Semantic roles, not raw colours: a value is OK/WARN/FAULT/MUTED/NEUTRAL first,
and only incidentally a particular hex code. If amber means "approaching the
peak rating" on one tab, `WARN` must mean that everywhere else too — that is
the whole point of centralising this instead of inlining hex per tab.
"""

OK      = "#1a7a1a"   # nominal / connected / in range
WARN    = "#c47a00"   # approaching a limit — advisory, not yet a fault
FAULT   = "#c0392b"   # out of spec, limit tripped, or a bad reading
MUTED   = "#bbbbbb"   # disabled controls
NEUTRAL = "#555555"   # default secondary text / disconnected status

# Slit colours reused across the beam-position indicator, the log-amp plot
# legend, and the amplifier tab so the same slit always reads as the same
# colour everywhere it appears.
SLIT_COLORS = {
    "X+": "#e74c3c",
    "X-": "#3498db",
    "Y+": WARN,
    "Y-": OK,
}


def status_label(role: str, bold: bool = True) -> str:
    """Stylesheet string for a label whose colour encodes OK/WARN/FAULT/etc.

    `role` is one of this module's colour constants (or any hex string).
    """
    weight = "bold" if bold else "normal"
    return f"color: {role}; font-weight: {weight};"


def pill(connected: bool) -> str:
    """Stylesheet for the recurring '● Connected' / '● Disconnected' label."""
    return status_label(OK if connected else NEUTRAL, bold=True)
