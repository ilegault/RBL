"""CSV writer for the edge-return test: one row per reading, deltas per speed."""
from datetime import datetime

from rbl.services.edge_return_writer import write_edge_return_csv


def test_edge_return_csv_rows(tmp_path):
    path = write_edge_return_csv(
        "C", [(225, [100, 102]), (58, [101])],
        directory=tmp_path, now=datetime(2026, 11, 17, 9, 5, 7))
    assert path.name == "edge_return_C_20261117_090507.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "axis,slit,pass_speed_cps,cycle,edge_counts,delta_from_first_counts"
    assert lines[1:] == ["C,Y+,225,1,100,0", "C,Y+,225,2,102,2", "C,Y+,58,1,101,0"]
