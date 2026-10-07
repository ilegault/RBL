"""
archive_pre_adr_0007.py
Archive data and configuration stores written with the old current monitor scale.

WHY THIS EXISTS
---------------
Before ADR 0007, RBL computed every amplifier current using 1 V = 10 mA from the
EEL5000 manual. On 2026-10-07 the manufacturer confirmed that the scale is
actually 1 V = 2 mA (ADR 0007). As a consequence, every past recorded current,
load capacitance, noise measurement and current threshold was 5x too high.

This script moves those pre-ADR-0007 data files and config stores aside into an
archive directory accompanied by a README explaining why. It never rescales and
never deletes data. It supports dry-run inspection by default and refuses to run
twice.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Sequence

from rbl.config.paths import CONFIG_DIR, LOG_ROOT

ARCHIVE_SUBDIR_NAME = "pre-2026-10-07-current-scale"

DATA_STORE_NAMES = (
    "trip_history.jsonl",
    "dynamic_adjustment_history.jsonl",
    "conditioning_history.jsonl",
    "calibration",
    "load_characterization",
)

README_TEXT = (
    "Computed with the EEL5000 manual's 1 V = 10 mA current-monitor scale. "
    "The manufacturer says the scale is 1 V = 2 mA "
    "(docs/adr/0007-current-monitor-scale-is-2-ma-per-volt.md). "
    "Every current, capacitance and current threshold in these files is 5x too high. "
    "Deliberately not rescaled."
)


def plan_archive(log_root: Path, config_dir: Path) -> list[tuple[Path, Path]]:
    """Determine (source, destination) move pairs for pre-ADR-0007 stores.

    Pure function: reads only its arguments, never Path.home() or environment
    variables. Only sources that currently exist are included in the returned
    list.
    """
    pairs: list[tuple[Path, Path]] = []

    # 1. Data stores under log_root / "data"
    data_dir = log_root / "data"
    data_archive_dir = data_dir / "archive" / ARCHIVE_SUBDIR_NAME

    for name in DATA_STORE_NAMES:
        src = data_dir / name
        if src.exists():
            dst = data_archive_dir / name
            pairs.append((src, dst))

    # 2. Config store
    cfg_src = config_dir / "load_calibration.json"
    if cfg_src.exists():
        cfg_dst = config_dir / "archive" / ARCHIVE_SUBDIR_NAME / "load_calibration.json"
        pairs.append((cfg_src, cfg_dst))

    # 3. Spike files: every spikes.csv anywhere under log_root, skipping
    # anything already under an archive directory.
    if log_root.exists():
        for spikes_csv in sorted(log_root.rglob("spikes.csv")):
            try:
                rel = spikes_csv.relative_to(log_root)
            except ValueError:
                rel = spikes_csv

            if "archive" in rel.parts:
                continue

            # Skip if spikes.csv is inside a directory already planned to move
            if any(src.is_dir() and src in spikes_csv.parents for src, _ in pairs):
                continue

            pairs.append((spikes_csv, spikes_csv.parent / "spikes.pre-adr-0007.csv"))

            spikes_dir = spikes_csv.parent / "spikes"
            if spikes_dir.is_dir():
                pairs.append((spikes_dir, spikes_csv.parent / "spikes.pre-adr-0007"))

    return pairs


def apply_archive(
    pairs: Sequence[tuple[Path, Path]],
    readme_dirs: Sequence[Path],
) -> None:
    """Create archive directories, write READMEs, and move listed pairs."""
    for d in readme_dirs:
        d.mkdir(parents=True, exist_ok=True)
        readme_path = d / "README.txt"
        readme_path.write_text(README_TEXT, encoding="utf-8")

    for src, dst in pairs:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(src, dst)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for pre-ADR-0007 data archival."""
    parser = argparse.ArgumentParser(
        description="Archive data and config stores written with the pre-ADR-0007 current scale."
    )
    parser.add_argument(
        "--log-root",
        type=Path,
        default=LOG_ROOT,
        help="Path to log root directory (default: %(default)s)",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=CONFIG_DIR,
        help="Path to config directory (default: %(default)s)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the archive moves instead of a dry run",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)
    log_root: Path = Path(args.log_root)
    config_dir: Path = Path(args.config_dir)

    data_archive_dir = log_root / "data" / "archive" / ARCHIVE_SUBDIR_NAME
    config_archive_dir = config_dir / "archive" / ARCHIVE_SUBDIR_NAME
    readme_dirs = [data_archive_dir, config_archive_dir]

    pairs = plan_archive(log_root, config_dir)

    if not args.apply:
        for src, dst in pairs:
            print(f"would move {src} -> {dst}")
        print("dry run: nothing moved; run again with --apply")
        return 0

    # With --apply:
    # 1. If either archive folder already exists, refuse the run
    for archive_dir in readme_dirs:
        if archive_dir.exists():
            print(f"already archived: {archive_dir}; refusing to run twice")
            return 1

    # 2. If any destination exists, refuse the whole run the same way
    for _, dst in pairs:
        if dst.exists():
            print(f"already archived: {dst}; refusing to run twice")
            return 1

    # 3. Otherwise move, then print moved <src> -> <dst> per pair
    apply_archive(pairs, readme_dirs)
    for src, dst in pairs:
        print(f"moved {src} -> {dst}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
