"""
tests/test_csv_log_writer.py

Unit tests for rbl.services.csv_log_writer.

The schema-roll behaviour exists because silently dropping a late-connecting
instrument's columns was a real data-loss bug on 8-hour irradiation runs.
These tests document and guard that guarantee.

All tests use a tmp_path fixture so nothing touches the real filesystem.
"""
import csv
import math
import os

import pytest

from rbl.services.csv_log_writer import CsvLogWriter, flatten

# ---------------------------------------------------------------------------
# flatten() — pure helper
# ---------------------------------------------------------------------------

class TestFlatten:
    def test_flat_dict_unchanged(self):
        assert flatten({"a": 1, "b": 2}) == {"a": 1, "b": 2}

    def test_nested_dict_dot_separated(self):
        result = flatten({"x": {"y": 3}})
        assert result == {"x.y": 3}

    def test_deeply_nested(self):
        result = flatten({"a": {"b": {"c": 7}}})
        assert result == {"a.b.c": 7}

    def test_short_list_indexed(self):
        result = flatten({"vals": [1, 2, 3]})
        assert result == {"vals[0]": 1, "vals[1]": 2, "vals[2]": 3}

    def test_short_list_boundary_8(self):
        """Exactly 8 elements → still flattened."""
        result = flatten({"v": list(range(8))})
        assert len(result) == 8

    def test_long_list_skipped(self):
        """More than 8 elements → silently skipped (waveform samples)."""
        result = flatten({"wave": list(range(9))})
        assert result == {}

    def test_nan_float_becomes_string(self):
        result = flatten({"x": float("nan")})
        assert result == {"x": "nan"}

    def test_positive_inf_becomes_string(self):
        result = flatten({"x": float("inf")})
        assert result == {"x": "inf"}

    def test_negative_inf_becomes_string(self):
        result = flatten({"x": float("-inf")})
        assert result == {"x": "-inf"}

    def test_normal_float_passed_through(self):
        result = flatten({"x": 3.14})
        assert result == {"x": 3.14}

    def test_string_value_passed_through(self):
        result = flatten({"s": "hello"})
        assert result == {"s": "hello"}

    def test_int_passed_through(self):
        result = flatten({"n": 42})
        assert result == {"n": 42}

    def test_none_passed_through(self):
        result = flatten({"x": None})
        assert result == {"x": None}

    def test_prefix_prepended(self):
        result = flatten({"y": 1}, prefix="root")
        assert result == {"root.y": 1}

    def test_tuple_treated_like_list(self):
        result = flatten({"t": (10, 20)})
        assert result == {"t[0]": 10, "t[1]": 20}

    def test_nested_dict_in_list(self):
        result = flatten({"items": [{"a": 1}, {"a": 2}]})
        assert result == {"items[0].a": 1, "items[1].a": 2}

    def test_out_dict_accumulates(self):
        out = {"pre": 0}
        result = flatten({"x": 1}, out=out)
        assert result is out
        assert result == {"pre": 0, "x": 1}

    def test_empty_dict(self):
        assert flatten({}) == {}

    def test_empty_list(self):
        assert flatten({"v": []}) == {}


# ---------------------------------------------------------------------------
# CsvLogWriter — file parts and naming
# ---------------------------------------------------------------------------

class TestCsvLogWriterParts:
    def test_first_part_named_data_csv(self, tmp_path):
        w = CsvLogWriter(str(tmp_path), "data")
        w.write({"a": 1})
        w.close()
        assert os.path.exists(str(tmp_path / "data.csv"))

    def test_custom_base_name(self, tmp_path):
        w = CsvLogWriter(str(tmp_path), "vacuum")
        w.write({"p": 1e-6})
        w.close()
        assert os.path.exists(str(tmp_path / "vacuum.csv"))

    def test_second_part_gets_002_suffix(self, tmp_path):
        w = CsvLogWriter(str(tmp_path))
        w.write({"a": 1})
        w.write({"a": 1, "b": 2})   # new column → roll
        w.close()
        assert os.path.exists(str(tmp_path / "data_002.csv"))

    def test_third_part_gets_003_suffix(self, tmp_path):
        w = CsvLogWriter(str(tmp_path))
        w.write({"a": 1})
        w.write({"a": 1, "b": 2})
        w.write({"a": 1, "b": 2, "c": 3})
        w.close()
        assert os.path.exists(str(tmp_path / "data_003.csv"))

    def test_parts_property_lists_all_basenames(self, tmp_path):
        w = CsvLogWriter(str(tmp_path))
        w.write({"a": 1})
        w.write({"a": 1, "b": 2})
        w.close()
        assert w.parts == ["data.csv", "data_002.csv"]

    def test_no_roll_when_schema_unchanged(self, tmp_path):
        w = CsvLogWriter(str(tmp_path))
        for _ in range(5):
            w.write({"a": 1, "b": 2})
        w.close()
        assert w.parts == ["data.csv"]


# ---------------------------------------------------------------------------
# CsvLogWriter — schema-roll (the core correctness guarantee)
# ---------------------------------------------------------------------------

class TestSchemaRoll:
    def test_roll_on_new_column(self, tmp_path):
        """A new column triggers a roll; both files have valid headers."""
        w = CsvLogWriter(str(tmp_path))
        w.write({"t": 0, "a": 1})
        w.write({"t": 1, "a": 2, "b": 99})   # b is new
        w.close()

        p1 = list(csv.DictReader(open(str(tmp_path / "data.csv"))))
        p2 = list(csv.DictReader(open(str(tmp_path / "data_002.csv"))))

        assert p1[0]["a"] == "1"
        assert "b" not in p1[0]              # b not in part 1's schema
        assert p2[0]["b"] == "99"            # b present in part 2

    def test_data_from_row_that_triggered_roll_is_in_new_file(self, tmp_path):
        """The row that caused the roll must land in the new part, not be lost."""
        w = CsvLogWriter(str(tmp_path))
        w.write({"t": 0})
        w.write({"t": 1, "x": 42})
        w.close()

        p2 = list(csv.DictReader(open(str(tmp_path / "data_002.csv"))))
        assert len(p2) == 1
        assert p2[0]["x"] == "42"

    def test_new_part_has_correct_header(self, tmp_path):
        w = CsvLogWriter(str(tmp_path))
        w.write({"a": 1})
        w.write({"a": 2, "b": 3, "c": 4})
        w.close()

        with open(str(tmp_path / "data_002.csv")) as f:
            reader = csv.DictReader(f)
            assert set(reader.fieldnames) == {"a", "b", "c"}

    def test_no_roll_on_subset_keys(self, tmp_path):
        """Row missing some current columns: no roll, missing → empty string."""
        w = CsvLogWriter(str(tmp_path))
        w.write({"a": 1, "b": 2})
        w.write({"a": 3})          # b missing — must NOT roll
        w.close()

        assert w.parts == ["data.csv"]
        rows = list(csv.DictReader(open(str(tmp_path / "data.csv"))))
        assert rows[1]["b"] == ""   # gap-filled with empty string

    def test_missing_keys_gap_filled(self, tmp_path):
        """Keys in header but absent from a row → empty string in output."""
        w = CsvLogWriter(str(tmp_path))
        w.write({"a": 1, "b": 2, "c": 3})
        w.write({"a": 4})
        w.close()

        rows = list(csv.DictReader(open(str(tmp_path / "data.csv"))))
        assert rows[1]["b"] == ""
        assert rows[1]["c"] == ""

    def test_multiple_rolls(self, tmp_path):
        """Three distinct schemas → three file parts."""
        w = CsvLogWriter(str(tmp_path))
        w.write({"a": 1})
        w.write({"a": 1, "b": 2})
        w.write({"a": 1, "b": 2, "c": 3})
        w.close()

        assert len(w.parts) == 3
        for name in ("data.csv", "data_002.csv", "data_003.csv"):
            assert os.path.exists(str(tmp_path / name))


# ---------------------------------------------------------------------------
# CsvLogWriter — flush, row count, close
# ---------------------------------------------------------------------------

class TestCsvLogWriterIO:
    def test_row_count_tracks_all_parts(self, tmp_path):
        w = CsvLogWriter(str(tmp_path))
        w.write({"a": 1})
        w.write({"a": 2, "b": 3})  # roll → part 2, still counts
        w.write({"a": 4, "b": 5})
        assert w.row_count == 3
        w.close()

    def test_file_readable_without_close(self, tmp_path):
        """flush-after-every-row: file must be readable mid-run."""
        w = CsvLogWriter(str(tmp_path))
        w.write({"x": 99})
        # Don't call close(); file must already contain data
        rows = list(csv.DictReader(open(str(tmp_path / "data.csv"))))
        assert rows[0]["x"] == "99"
        w.close()

    def test_close_is_idempotent(self, tmp_path):
        """Calling close() twice must not raise."""
        w = CsvLogWriter(str(tmp_path))
        w.write({"a": 1})
        w.close()
        w.close()   # second close — must not raise

    def test_header_written_to_first_row(self, tmp_path):
        w = CsvLogWriter(str(tmp_path))
        w.write({"foo": 1, "bar": 2})
        w.close()
        with open(str(tmp_path / "data.csv")) as f:
            first_line = f.readline().strip()
        assert "foo" in first_line and "bar" in first_line

    def test_all_rows_readable_after_close(self, tmp_path):
        w = CsvLogWriter(str(tmp_path))
        for i in range(10):
            w.write({"i": i})
        w.close()
        rows = list(csv.DictReader(open(str(tmp_path / "data.csv"))))
        assert len(rows) == 10
        assert [int(r["i"]) for r in rows] == list(range(10))

    def test_first_write_determines_schema(self, tmp_path):
        w = CsvLogWriter(str(tmp_path))
        w.write({"z": 9, "a": 1})
        w.write({"z": 8, "a": 2})
        w.close()
        with open(str(tmp_path / "data.csv")) as f:
            header = f.readline().strip()
        # Header columns must be the keys from the first row
        assert "z" in header and "a" in header

    def test_ieee_special_values_written_as_strings(self, tmp_path):
        """NaN written via flatten then write must appear as 'nan' not bare."""
        w = CsvLogWriter(str(tmp_path))
        row = flatten({"v": float("nan"), "w": float("inf")})
        w.write(row)
        w.close()
        rows = list(csv.DictReader(open(str(tmp_path / "data.csv"))))
        assert rows[0]["v"] == "nan"
        assert rows[0]["w"] == "inf"
