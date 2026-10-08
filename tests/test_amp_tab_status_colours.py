"""Tests for amp_tab status colours mapping."""
from rbl.gui import amp_tab
from rbl.hardware import amp_monitor


def test_every_current_status_has_a_colour():
    for status in amp_monitor.CURRENT_STATUSES:
        assert status in amp_tab.STATUS_COLOR
