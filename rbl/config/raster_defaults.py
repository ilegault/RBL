"""
raster_defaults.py
Opening values for the Raster Planner's input boxes.

WHY THIS EXISTS
---------------
These nine numbers used to be literals inside raster_planner_tab.__init__ —
`_spin(0.1, 10_000.0, 517.0, ...)` and eight more like it — which meant the
answer to "where do I change the default X frequency?" was "read a GUI
constructor."  Every other default this tab uses already lives in a config
module (steerer_geometry, calibration_config, beam_species); these are the
stragglers.

These are OPENING VALUES, not constraints.  Every one is editable in the tab
and nothing downstream reads them again.
"""

FWHM_MM_DEFAULT        = 1.0
SAMPLE_WIDTH_X_MM      = 10.0
SAMPLE_HEIGHT_Y_MM     = 10.0
OFFSET_X_MM_DEFAULT    = 0.0
OFFSET_Y_MM_DEFAULT    = 0.0
TURNAROUND_K_DEFAULT   = 1.5      # multiples of FWHM beyond the sample edge
FREQ_X_HZ_DEFAULT      = 517.0   # fast axis
FREQ_Y_HZ_DEFAULT      = 64.0    # slow axis

AMP_MAX_BANDWIDTH_HZ   = 10_000.0  # EEL5000 large-signal BW, no load (manual p.1-3)


if __name__ == "__main__":
    import math
    for name, val in (
        ("FWHM_MM_DEFAULT",      FWHM_MM_DEFAULT),
        ("SAMPLE_WIDTH_X_MM",    SAMPLE_WIDTH_X_MM),
        ("SAMPLE_HEIGHT_Y_MM",   SAMPLE_HEIGHT_Y_MM),
        ("TURNAROUND_K_DEFAULT", TURNAROUND_K_DEFAULT),
        ("FREQ_X_HZ_DEFAULT",    FREQ_X_HZ_DEFAULT),
        ("FREQ_Y_HZ_DEFAULT",    FREQ_Y_HZ_DEFAULT),
        ("AMP_MAX_BANDWIDTH_HZ", AMP_MAX_BANDWIDTH_HZ),
    ):
        assert math.isfinite(val) and val > 0, f"{name} must be finite and positive"
    # Offsets may be zero
    assert math.isfinite(OFFSET_X_MM_DEFAULT)
    assert math.isfinite(OFFSET_Y_MM_DEFAULT)
    print("[OK] raster_defaults self-test passed")
