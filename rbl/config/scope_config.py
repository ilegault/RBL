"""
scope_config.py
Configuration constants for the Tektronix TDS 2012 oscilloscope.

Keep numeric values here and nowhere else.  Never import this from
hardware drivers — the driver layer is instrument-protocol only.
"""

# Serial communication
TDS_BAUD_DEFAULT        = 19200      # scope front-panel default; also 9600
TDS_TIMEOUT_S           = 5.0        # per-read serial timeout (CURVE? can be slow)

# Acquisition
SCOPE_CHANNEL_DEFAULT   = "CH1"      # channel to acquire; "CH2" if preferred
SCOPE_POLL_INTERVAL_S   = 1.0        # seconds between successive acquisitions

# Reconnect ladder (seconds between attempts after a serial error)
SCOPE_RECONNECT_BACKOFF_S = [1.0, 2.0, 5.0, 10.0, 30.0]

# Waveform display downsampling — max points sent to the GUI plot.
# The scope returns up to 2500 samples; 500 is more than enough for a display.
SCOPE_WAVEFORM_DOWNSAMPLE = 500


# ---------------------------------------------------------------------------
# Profile analysis
#
# The beam sweep crosses the aperture TWICE per period - once outbound, once
# on the way back - so one channel carries two peaks of the same beam.  Both
# are measured independently and their widths should agree; the disagreement
# is reported as a spread and is a quality signal, not a second measurement.
# ---------------------------------------------------------------------------

SCOPE_EXPECTED_PEAKS    = 2        # peaks per trace (1..SCOPE_MAX_PEAKS)
SCOPE_MAX_PEAKS         = 4        # hard ceiling the analysis will report

# Boxcar width, in samples, applied before the half-maximum search.
# Measured on the real beam line: at S/N ~ 12 an unsmoothed half-maximum
# reading came out ~30 % narrow, because noise on top of the peak lifts the
# apparent peak height and therefore the half-maximum level.  9-25 samples
# costs nothing on a profile that is hundreds of samples wide.
SCOPE_SMOOTH_WINDOW     = 15

# A local maximum shorter than this fraction of the tallest peak is noise,
# not a beam crossing.
SCOPE_PEAK_THRESHOLD    = 0.30

# Two maxima closer together than this fraction of the record are the same
# peak seen twice through noise.
SCOPE_PEAK_MIN_SEP_FRAC = 0.02

# Sweeps averaged BY THE SCOPE (4/16/64/128).  None leaves the scope's
# acquisition mode alone; 1 forces Sample mode.
SCOPE_AVERAGE_SWEEPS    = 16

# The Gaussian fit is preferred over the half-maximum reading when its r^2
# clears this bar.  The fit uses every sample; the half-maximum reading uses
# four per peak, so on a noisy beam the fit is the better number - but only
# while the model actually describes the data.
SCOPE_FIT_MIN_R2        = 0.90

# Signal polarity: "auto" picks the larger excursion, "pos"/"neg" force it.
SCOPE_POLARITY          = "auto"

# What the two peaks ARE, left to right.  They are the X profile and the Y
# profile - two directions in one trace, not one beam measured twice.  Swap
# this pair if the sweep order is reversed; the labels drive every readout.
SCOPE_AXIS_LABELS       = ("X", "Y")

# Raster period in MILLISECONDS.  A rastered beam arrives as a train of
# raster teeth whose envelope carries the beam width; measuring half maximum
# on the raw trace measures one tooth.  Set this to the raster period to
# switch the analysis onto the envelope; 0 disables it.
#
# Match it to the ripple you can see on the trace.  A window WIDER than the
# ripple broadens the profile and reads high (2x the period read ~5 % wide
# in test); narrower than the ripple lets teeth through.
SCOPE_ENVELOPE_MS       = 0.0


# ---------------------------------------------------------------------------
# Transfer size
#
# READ THIS BEFORE CHANGING SCOPE_POINTS.  On the TDS 2012, DATA:START and
# DATA:STOP select a CONTIGUOUS SLICE of the 2500-point record.  They do not
# decimate.  Asking for 1000 points does not give you a coarser view of the
# whole screen - it gives you 1000 consecutive samples and throws the rest
# away, so you are looking at 40 % of the time window at full resolution.
#
# That is the trade: the transfer is the slow part (2500 bytes at 19200 baud
# is over a second before the preamble and command overhead), and the only
# way to make it shorter is to carry less of the record.  If a peak lives
# outside the slice, it is simply not in the data - so shorten the timebase
# to match, or leave this at full.
# ---------------------------------------------------------------------------

SCOPE_RECORD_POINTS  = 2500        # the scope's full record - a hardware fact
SCOPE_POINTS         = 2500        # points actually transferred per acquisition
SCOPE_POINTS_CHOICES = (2500, 1250, 1000, 625, 500, 250)

# Where the slice sits in the record: "centre" keeps the middle of the
# screen, "start" keeps the left edge.  Centre is the default because a beam
# is usually positioned around the trigger.
SCOPE_POINTS_ANCHOR  = "centre"

# Seconds between acquisitions IN CONTINUOUS MODE.  The cheapest way to
# lighten the load: the transfer time per acquisition is unchanged, there
# are just fewer of them.
SCOPE_POLL_CHOICES   = (0.0, 1.0, 2.0, 5.0, 10.0, 30.0)

# ---------------------------------------------------------------------------
# Acquisition mode
#
# SINGLE SHOT IS THE DEFAULT, and that is a deliberate reversal.  A 2500-point
# transfer takes over a second; free-running, the link is busy essentially all
# the time and the analysis and redraw ride on top of it.  Connecting used to
# start that immediately - so the app went sluggish the moment you connected,
# including while rastering, when the trace being fetched over and over was
# not telling anyone anything.
#
# Reading a beam profile is a thing an operator DOES at a moment of their
# choosing: set the beam up, take a shot, read it.  Continuous is still there
# for watching a number drift, but it is now something you turn on rather
# than something you have to fight off.
# ---------------------------------------------------------------------------

SCOPE_CONTINUOUS_DEFAULT = False


# ---------------------------------------------------------------------------
# BPM SCOPE CALIBRATION  (milliseconds -> millimetres)
#
# WHAT IS BEING CALIBRATED, AND WHY IT IS A RATE
# ----------------------------------------------
# The BPM sweeps a wire across the beam.  The scope therefore measures the
# profile in TIME, and the number that turns that into a real size is the
# speed the wire crosses the aperture at - millimetres of real space per
# second of trace.  That is a property of the BPM's own scan, not of the
# oscilloscope, so ONE calibration holds no matter what the timebase is set
# to afterwards.  This is the whole reason the app does not follow the
# written procedure's "adjust until the peaks are 6 divisions apart" step:
# divisions exist because a human reads them off the screen.  The app reads
# XINCR out of the waveform preamble and knows the real time between the
# fiducial peaks to the sample, so it skips straight to the rate.
#
# THE FIDUCIAL MARKS
# ------------------
# With the BPM controller's output selector set to fiducial marks, the trace
# carries the X and Y calibration peaks.  On a BPM80 those are 6 cm apart in
# real space - a mechanical fact of the head, from the NEC manual - so
#
#     mm per second = BPM_FIDUCIAL_SPACING_MM / (separation in seconds)
#
# and every width the profiler measures can be multiplied by it.
#
# THE THIRD PEAK IS THE TRIGGER AND MUST NOT BE MEASURED.
# The procedure warns about this in so many words.  It is normally the
# TALLEST thing on the trace, which is what BPM_CAL_TRIGGER_RULE keys off.
# ---------------------------------------------------------------------------

# Real-space distance between the X and Y calibration peaks, in mm.
# 6 cm on every NEC BPM80, which is every BPM on this beam line.
BPM_FIDUCIAL_SPACING_MM = 60.0

# Peaks to look for on a fiducial trace: the two calibration marks plus the
# trigger peak.  Raise it if the trace carries more furniture than that.
BPM_CAL_EXPECTED_PEAKS  = 3

# How the trigger peak is told apart from the fiducials.  "tallest" drops
# the highest peak; "first"/"last" drop by position.  The written procedure
# describes the trigger as the taller peak, so "tallest" is the default.
BPM_CAL_TRIGGER_RULE    = "tallest"

# The auto-pick is only trustworthy while the trigger really does stand
# clear of the fiducials.  If the tallest peak is less than this fraction
# above the tallest fiducial, the result is reported as UNCONFIRMED and the
# operator is asked to check the marked peaks - it is not silently used.
BPM_CAL_TRIGGER_MARGIN  = 0.15      # 15 %

# Analysis settings used WHILE IN CALIBRATION MODE.  Fiducial marks are
# narrow, clean and identical every sweep, so they want far less smoothing
# than a beam profile and a lower detection threshold (the fiducials sit
# well below the trigger peak, and a 30 % bar can lose them).
BPM_CAL_SMOOTH_WINDOW   = 5
BPM_CAL_PEAK_THRESHOLD  = 0.12
BPM_CAL_MIN_SEP_FRAC    = 0.01

# Names offered in the BPM dropdown.  The list is editable in the app and
# whatever is typed is remembered, so this is a starting point, not a limit.
BPM_NAMES_DEFAULT       = ("BPM 1", "BPM 2", "BPM 3", "BPM 4")


# ---------------------------------------------------------------------------
# THE WIDTH LADDER
#
# FWHM describes the core and says nothing about where the beam ends.  That
# matters here specifically: slit_raster_model derives the overscan needed
# for a given edge droop by inverting a NORMAL CDF from the FWHM alone, so
# every droop figure the raster planner prints rests on the beam being
# Gaussian.  Measuring a second and third level per shot puts that
# assumption on trial instead of assuming it.
#
# For a true Gaussian:  FW(f)/FWHM = sqrt(ln(1/f)/ln 2)
#     13.5335 % (1/e^2) -> 1.69864   (exactly 4 sigma - the optics convention)
#     10 %     (tenth)  -> 1.82261
#
# A measured FWTM/FWHM above 1.8226 is heavier tails than Gaussian (halo,
# and the planner is optimistic); below it is a flat-topped or scraped
# profile.  With quadrupole focusing the shape is not consistent between
# sessions, so every level is measured fresh on every trace and nothing is
# carried forward.
# ---------------------------------------------------------------------------

SCOPE_WIDTH_LEVELS = (0.50, 0.13533528323661270, 0.10)

# How many noise sigmas a level must clear to be measurable at all.  Below
# this, a "crossing" is a crossing of the noise.
SCOPE_LEVEL_NOISE_GUARD = 3.0

# How far the measured FWTM/FWHM may sit from the Gaussian 1.8226 before the
# tab and the raster planner call it out.  5 % is about where the difference
# starts to move an overscan figure by something an operator would notice.
SCOPE_TAIL_TOLERANCE = 0.05
