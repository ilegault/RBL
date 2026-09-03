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

# Chrome for the scaled readouts (mini bar charts, sparklines): the groove a
# value sits in, its outline, tick marks, and the marker drawn at a setpoint.
# Deliberately low-contrast — the value is the subject, the scale is context.
TRACK      = "#d4d4d4"
TRACK_EDGE = "#8a8a8a"
TICK       = "#7a7a7a"
MARKER     = "#1a1a1a"

# Slit colours reused across the beam-position indicator, the log-amp plot
# legend, and the amplifier tab so the same slit always reads as the same
# colour everywhere it appears.
SLIT_COLORS = {
    "X+": "#e74c3c",
    "X-": "#3498db",
    "Y+": WARN,
    "Y-": OK,
}


# ── Overview type scale ───────────────────────────────────────────────────────
#
# The Overview is read from across the room — it is the "is the beamline OK?"
# screen, not a screen anyone sits at to type. Everything on it was sized for
# a seated operator (9-12 px) and is illegible from a few feet away, so it gets
# its own scale, ~1.4x the old sizes, defined once here rather than as a hex of
# hard-coded px values scattered through four widget files.
#
# Sizes are px (Qt stylesheet units), not pt, because that is what the
# stylesheets around them already used; the ratios are what matter.
FS_TINY    = 12   # scale captions under a bar's tick marks
FS_CAPTION = 13   # a widget's name, a footnote under a trace
FS_LABEL   = 14   # form labels, standing help text
FS_VALUE   = 17   # a live number inside a bar chart
FS_BIG     = 19   # a headline number or a group total


def status_label(role: str, bold: bool = True) -> str:
    """Stylesheet string for a label whose colour encodes OK/WARN/FAULT/etc.

    `role` is one of this module's colour constants (or any hex string).
    """
    weight = "bold" if bold else "normal"
    return f"color: {role}; font-weight: {weight};"


def pill(connected: bool) -> str:
    """Stylesheet for the recurring '● Connected' / '● Disconnected' label."""
    return status_label(OK if connected else NEUTRAL, bold=True)
