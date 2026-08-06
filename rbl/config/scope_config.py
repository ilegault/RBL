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
