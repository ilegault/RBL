"""CSV writer for the edge-return test: one row per reading, deltas per speed."""
from datetime import datetime

from rbl.hardware.edge_return import EdgeReturnReading
from rbl.services.edge_return_writer import CSV_COLUMNS, write_edge_return_csv


def test_edge_return_csv_rows(tmp_path):
    path = write_edge_return_csv(
        "C",
        [
            (225, [EdgeReturnReading(95, 100), EdgeReturnReading(97, 102)]),
            (58, [EdgeReturnReading(96, 101)]),
        ],
        directory=tmp_path,
        now=datetime(2026, 11, 17, 9, 5, 7),
    )
    assert path.name == "edge_return_C_20261117_090507.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == (
        "axis,slit,pass_speed_cps,cycle,stop_counts,"
        "stop_delta_from_first_counts,edge_counts,edge_delta_from_first_counts"
    )
    assert lines[1:] == [
        "C,Y+,225,1,95,0,100,0",
        "C,Y+,225,2,97,2,102,2",
        "C,Y+,58,1,96,0,101,0",
    ]


def test_edge_return_csv_has_stop_and_edge_columns(tmp_path):
    run_600 = [
        EdgeReturnReading(-9, 6),
        EdgeReturnReading(-17, -2),
        EdgeReturnReading(-16, 0),
    ]
    run_900 = [
        EdgeReturnReading(-12, 5),
        EdgeReturnReading(-12, 6),
        EdgeReturnReading(-11, 5),
    ]
    path = write_edge_return_csv(
        "A",
        [(600, run_600), (900, run_900)],
        directory=tmp_path,
        now=datetime(2026, 11, 23, 12, 0, 0),
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    expected_cols = [
        "axis",
        "slit",
        "pass_speed_cps",
        "cycle",
        "stop_counts",
        "stop_delta_from_first_counts",
        "edge_counts",
        "edge_delta_from_first_counts",
    ]
    assert CSV_COLUMNS == expected_cols
    assert lines[0].split(",") == expected_cols
    assert lines[1:] == [
        "A,X+,600,1,-9,0,6,0",
        "A,X+,600,2,-17,-8,-2,-8",
        "A,X+,600,3,-16,-7,0,-6",
        "A,X+,900,1,-12,0,5,0",
        "A,X+,900,2,-12,0,6,1",
        "A,X+,900,3,-11,1,5,0",
    ]
