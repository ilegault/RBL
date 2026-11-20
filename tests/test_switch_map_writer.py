"""CSV writer for the switch map measurement."""
from datetime import datetime

from rbl.hardware.switch_map import SwitchMapRow
from rbl.services.switch_map_writer import write_switch_map_csv


def test_switch_map_csv_rows(tmp_path):
    rows = [
        SwitchMapRow(
            step=1, direction="in", position_counts=-3, home=True, reverse=True, forward=False
        ),
        SwitchMapRow(
            step=2, direction="out", position_counts=6, home=False, reverse=False, forward=False
        ),
    ]
    path = write_switch_map_csv(
        "B",
        rows,
        directory=tmp_path,
        now=datetime(2026, 11, 18, 9, 5, 7),
    )

    assert path.name == "switch_map_B_20261118_090507.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines == [
        "# axis=B slit=X- step_counts=1 step_speed_cps=900",
        "step,direction,position_counts,home,reverse,forward",
        "1,in,-3,1,1,0",
        "2,out,6,0,0,0",
    ]
