"""
csv_log_writer.py
Append-only CSV writer with automatic schema-roll on column expansion.

The old logger_widget.py used csv.DictWriter with extrasaction="ignore": if a
new instrument connected mid-run its columns were silently discarded for the
rest of the session.  This is a real data-loss bug on 8-hour irradiation runs
where a vacuum gauge or scope might connect an hour in.

CsvLogWriter fixes this by detecting new keys on every write and rolling to a
new file part when the schema grows.  Each part gets its own header derived
from its first row.  Parts are named:

    data.csv          (first part — no suffix)
    data_002.csv      (second part)
    data_003.csv      ...

The caller is responsible for prepending t_rel_s, wall_utc, and frame_index
to each row dict before calling write().  This class handles everything else:
header derivation, gap-filling (keys present in the header but absent from a
row are written as empty strings), flushing after every row, and rolling.

flatten() is the _flatten() function from logger_widget.py, moved here so both
the session recorder and any future consumer share one canonical implementation.
It skips arrays longer than 8 elements (waveform samples) and converts IEEE
special floats (NaN, Inf) to "nan"/"inf" strings so downstream spreadsheet
tools do not choke on them.
"""
import csv
import math
import os

# ---- Public helpers --------------------------------------------------------

def flatten(obj, prefix: str = "", out: dict | None = None) -> dict:
    """Recursively flatten a nested dict/list into dot-separated scalar keys.

    Arrays with more than 8 elements (e.g. waveform samples) are skipped —
    they are not meaningful as individual CSV columns.  Scalars that are
    NaN or Inf are written as the string "nan"/"inf" so downstream tools
    do not choke on bare IEEE special values.
    """
    if out is None:
        out = {}

    if isinstance(obj, dict):
        for k, v in obj.items():
            flatten(v, f"{prefix}.{k}" if prefix else k, out)
    elif isinstance(obj, (list, tuple)):
        if len(obj) <= 8:
            for i, v in enumerate(obj):
                flatten(v, f"{prefix}[{i}]", out)
        # else: skip large arrays (waveform samples)
    elif isinstance(obj, float):
        if math.isnan(obj):
            out[prefix] = "nan"
        elif math.isinf(obj):
            out[prefix] = "inf" if obj > 0 else "-inf"
        else:
            out[prefix] = obj
    else:
        out[prefix] = obj

    return out


# ---- Writer ----------------------------------------------------------------

class CsvLogWriter:
    """Append-only, flush-every-row CSV writer with schema-roll support.

    Parameters
    ----------
    folder    : directory in which to create the file(s)
    base_name : stem of the filename, default "data"
                Part 1 → "data.csv", part 2 → "data_002.csv", ...
    """

    def __init__(self, folder: str, base_name: str = "data"):
        self._folder    = folder
        self._base_name = base_name
        self._part      = 1
        self._parts: list[str] = []
        self._header: list[str] | None = None
        self._file   = None
        self._writer = None
        self._rows   = 0
        self._open_part()

    # ---- public API --------------------------------------------------------

    def write(self, row: dict) -> None:
        """Append one row, rolling to a new part if the schema has grown."""
        keys = list(row.keys())

        if self._header is None:
            # First row — derive the header.
            self._header = keys
            self._writer = csv.DictWriter(
                self._file, fieldnames=self._header,
                extrasaction="ignore", lineterminator="\n")
            self._writer.writeheader()
        else:
            new_keys = [k for k in keys if k not in self._header]
            if new_keys:
                # Schema grew — roll to the next part.
                self.close()
                self._part += 1
                self._open_part()        # sets self._header = None
                self._header = keys      # override with new schema
                self._writer = csv.DictWriter(
                    self._file, fieldnames=self._header,
                    extrasaction="ignore", lineterminator="\n")
                self._writer.writeheader()

        # Keys in the header but missing from this row → empty string.
        out = {k: row.get(k, "") for k in self._header}
        self._writer.writerow(out)
        self._file.flush()
        self._rows += 1

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file   = None
            self._writer = None

    @property
    def row_count(self) -> int:
        return self._rows

    @property
    def parts(self) -> list[str]:
        """Basenames of all file parts written so far."""
        return list(self._parts)

    # ---- internal ----------------------------------------------------------

    def _open_part(self) -> None:
        if self._part == 1:
            name = f"{self._base_name}.csv"
        else:
            name = f"{self._base_name}_{self._part:03d}.csv"
        path = os.path.join(self._folder, name)
        self._file  = open(path, "w", newline="", encoding="utf-8")
        self._parts.append(name)
        self._header = None
        self._writer = None
