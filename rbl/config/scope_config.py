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
