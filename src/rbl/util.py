"""
util.py
Small shared utilities that don't belong in any instrument-specific layer.

WHY THIS EXISTS
---------------
A recurring pattern in teardown paths: call a shutdown step, but keep going if
it fails.  Without a helper, every such step is three lines of boilerplate that
silently swallows the exception.  Silently swallowing is the correct *action*
(teardown must continue), but silent *logging* is not — if a T7 fails to leave
stream mode, that is exactly what you need in the log the next time the app
won't reconnect.  best_effort() logs at WARNING so teardown failures are
captured to rbl.log without requiring caller discipline.
"""
import logging

log = logging.getLogger(__name__)


def best_effort(label: str, fn, *args, **kwargs):
    """Call fn(*args, **kwargs); log any exception at WARNING and continue.

    For teardown paths only.  Never use this to hide errors in normal
    operation — add real error handling there instead.
    """
    try:
        return fn(*args, **kwargs)
    except Exception:
        log.warning("shutdown: %s failed", label, exc_info=True)
