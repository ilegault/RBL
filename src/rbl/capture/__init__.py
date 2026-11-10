"""
rbl.capture
Package owning the external Capture Process and camera interfaces.

WHY THIS EXISTS
---------------
ADR 0008 establishes that the ZWO camera is owned entirely by an independent
Capture Process that imports no Qt and runs as plain Python with threads.
This isolates multi-megabyte frame acquisition, preview generation, and disk
writes from the main application's GUI event loop and steerer control.
The protocol between the GUI and Capture Process is language-neutral JSON over
standard I/O, allowing the Capture Process to be replaced by a compiled program
if desired.
"""
