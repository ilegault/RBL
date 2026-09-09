"""
tests/test_driver_preflight.py
Unit tests for driver preflight capability checks and retirement of bundled installers.

WHY THIS EXISTS
---------------
Ticket 02 retires the dead bundled-installer path while preserving the capability
checks (LabJack, VISA, USB-serial) that inform the operator of missing dependencies.
This test asserts:
- Helper functions for finding and launching installers are removed from rbl.driver.
- Preflight failure records do not contain an 'installer' key.
- VISA capability failure guidance explicitly names Keysight IO Libraries Suite.
- MainWindow preflight warning bar displays guidance without 'Install' buttons.
- build.bat and rbl.spec no longer contain vendor installer bundling logic.
"""
from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

from rbl import driver


def test_installer_helpers_removed_from_driver():
    """Installer lookup and launch helpers must be removed from rbl.driver."""
    removed_attributes = [
        "launch_installer",
        "_candidate_dirs",
        "_find_exe",
        "find_ljm_installer",
        "find_visa_installer",
        "find_serial_installer",
    ]
    for attr in removed_attributes:
        assert not hasattr(driver, attr), f"rbl.driver still defines {attr}"


def test_check_all_omits_installer_field():
    """Failure summaries returned by check_all must not include an 'installer' field."""
    with patch.object(driver, "check_ljm", return_value=(False, "LJM missing")), \
         patch.object(driver, "check_visa", return_value=(False, "VISA missing")), \
         patch.object(driver, "check_serial", return_value=(False, "Serial missing")):
        failures = driver.check_all()
        assert len(failures) == 3
        for item in failures:
            assert "name" in item
            assert "message" in item
            assert "key" in item
            assert "installer" not in item, f"Unexpected 'installer' field in {item}"


def test_check_visa_guidance_names_keysight():
    """When no VISA backend is installed, guidance text must name Keysight IO Libraries Suite."""
    mock_pyvisa = MagicMock()
    mock_pyvisa.ResourceManager.side_effect = Exception("No visa library found on the system")

    with patch.dict("sys.modules", {"pyvisa": mock_pyvisa}):
        ok, msg = driver.check_visa()
        assert not ok
        assert "Keysight IO Libraries Suite" in msg


def test_check_ljm_reports_correctly():
    """check_ljm returns (True, ...) if LJM answers, (False, ...) otherwise."""
    mock_ljm = MagicMock()
    mock_ljm.readLibraryConfigS.return_value = 1.2100
    mock_pkg = MagicMock()
    mock_pkg.ljm = mock_ljm

    with patch.dict("sys.modules", {"labjack": mock_pkg, "labjack.ljm": mock_ljm}):
        ok, msg = driver.check_ljm()
        assert ok
        assert "1.2100" in msg

    mock_ljm.readLibraryConfigS.side_effect = Exception("communication error")
    with patch.dict("sys.modules", {"labjack": mock_pkg, "labjack.ljm": mock_ljm}):
        ok, msg = driver.check_ljm()
        assert not ok
        assert "communication error" in msg


def test_check_serial_reports_correctly():
    """check_serial confirms pyserial can enumerate ports."""
    mock_serial = MagicMock()
    mock_port = MagicMock()
    mock_port.device = "COM3"
    mock_serial.tools.list_ports.comports.return_value = [mock_port]

    with patch.dict(
        "sys.modules",
        {"serial": mock_serial, "serial.tools.list_ports": mock_serial.tools.list_ports},
    ):
        ok, msg = driver.check_serial()
        assert ok
        assert "COM3" in msg


def test_gui_warning_bar_has_no_install_buttons():
    """MainWindow preflight warning bar displays guidance only, with no install buttons."""
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from rbl.gui.app import MainWindow

    app = QApplication.instance() or QApplication([])
    assert app is not None

    fake_failures = [
        {"name": "LabJack LJM", "message": "LJM missing", "key": "ljm"},
        {"name": "Keysight IO Libraries Suite", "message": "VISA missing", "key": "visa"},
        {"name": "USB-serial driver", "message": "Serial missing", "key": "serial"},
    ]

    with patch.object(driver, "check_all", return_value=fake_failures):
        win = MainWindow()

        # Confirm _install_prerequisite is removed from MainWindow
        assert not hasattr(win, "_install_prerequisite")

        # Confirm the VISA row guidance text mentions Keysight IO Libraries Suite
        labels = win.findChildren(QLabel)
        prereq_texts = " ".join(lbl.text() for lbl in labels)
        assert "Keysight IO Libraries Suite" in prereq_texts

        # There should be no button offering to install drivers
        buttons = [btn for btn in win.findChildren(QPushButton) if "Install" in btn.text()]
        assert len(buttons) == 0, f"Found unexpected install buttons: {buttons}"

        win.close()


def test_build_files_retire_bundled_installers():
    """build.bat and rbl.spec must no longer contain vendor installer bundling logic."""
    root = pathlib.Path(__file__).resolve().parent.parent

    build_bat = (root / "build.bat").read_text(encoding="utf-8", errors="ignore")
    assert "Copying vendor installers" not in build_bat
    assert 'dist\\RBL\\vendor' not in build_bat

    rbl_spec = (root / "rbl.spec").read_text(encoding="utf-8", errors="ignore")
    assert "vendor\\ installers — SHIPPED BESIDE THE APP, NOT INSIDE IT" not in rbl_spec
    assert "ANTIVIRUS_FALSE_POSITIVE.md" in rbl_spec
