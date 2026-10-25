"""Beginner starter for the Aeron weather-station assignment.

Current stage: inspect the data pack and create an empty flags.csv with the
required columns. This is NOT the finished checker yet. Add and test data-quality
rules only after you understand the actual files and column names.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd

FLAG_COLUMNS = [
    "station_id",
    "channel",
    "start_utc",
    "end_utc",
    "issue",
    "action",
    "note",
]

TABLE_EXTENSIONS = {".csv", ".tsv", ".xlsx", ".xls", ".json", ".parquet"}
TEXT_EXTENSIONS = {".txt", ".log", ".nmea", ".md"}


def read_table(path: Path) -> pd.DataFrame:
    """Read a common table format with reasonable defaults."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        try:
            return pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig", on_bad_lines="warn")
        except UnicodeDecodeError:
            return pd.read_csv(path, sep=None, engine="python", encoding="latin-1", on_bad_lines="warn")
    if suffix == ".tsv":
        return pd.read_csv(path, sep="\t", encoding="utf-8-sig", on_bad_lines="warn")
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    if suffix == ".json":
        try:
            return pd.read_json(path)
        except ValueError:
            return pd.read_json(path, lines=True)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    raise ValueError(f"Unsupported table format: {suffix}")


def inspect_pack(data_folder: Path) -> None:
    """Print an inventory of files and basic details of tabular files."""
    files = sorted(p for p in data_folder.rglob("*") if p.is_file())
    if not files:
        print("No files found. Check that you selected the actual data-pack folder.")
        return

    print(f"\nDATA PACK: {data_folder.resolve()}")
    print(f"Files found: {len(files)}\n")

    table_files = [p for p in files if p.suffix.lower() in TABLE_EXTENSIONS]
    text_files = [p for p in files if p.suffix.lower() in TEXT_EXTENSIONS]

    print("=== FILE LIST ===")
    for path in files:
        relative = path.relative_to(data_folder)
        print(f"- {relative} ({path.stat().st_size:,} bytes)")

    print("\n=== TABLE DETAILS ===")
    if not table_files:
        print("No supported table files found. Check the file list above and README/instructions in the pack.")
    for path in table_files:
        print(f"\nFile: {path.relative_to(data_folder)}")
        try:
            frame = read_table(path)
            print(f"Rows: {len(frame):,} | Columns: {len(frame.columns)}")
            print("Column names:")
            for col in frame.columns:
                missing = int(frame[col].isna().sum())
                print(f"  - {col!r} | dtype={frame[col].dtype} | missing={missing:,}")
            print("First 3 rows (for local inspection):")
            print(frame.head(3).to_string(index=False, max_cols=12))
        except Exception as exc:  # Keep inspecting the rest if one file fails.
            print(f"Could not read this file automatically: {type(exc).__name__}: {exc}")

    print("\n=== TEXT FILE NOTES ===")
    for path in text_files:
        print(f"\nFile: {path.relative_to(data_folder)}")
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                lines = [line.rstrip() for _, line in zip(range(5), handle) if line.strip()]
            if lines:
                print("First non-empty lines:")
                for line in lines:
                    print(f"  {line[:180]}")
            else:
                print("(No non-empty lines in the first section.)")
        except OSError as exc:
            print(f"Could not read text preview: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect an Aeron-format data pack and create a starter flags.csv."
    )
    parser.add_argument("data_folder", help="Path to the folder containing the supplied data pack")
    args = parser.parse_args()

    data_folder = Path(args.data_folder).expanduser()
    if not data_folder.exists():
        print(f"ERROR: Folder does not exist: {data_folder}", file=sys.stderr)
        return 2
    if not data_folder.is_dir():
        print(f"ERROR: This path is a file, not a folder: {data_folder}", file=sys.stderr)
        return 2

    inspect_pack(data_folder)

    # Assignment-required output file. It is intentionally empty at this stage;
    # detection rules must be added and validated against the supplied data.
    output_path = Path.cwd() / "flags.csv"
    pd.DataFrame(columns=FLAG_COLUMNS).to_csv(output_path, index=False)
    print(f"\nCreated starter file: {output_path.resolve()}")
    print("IMPORTANT: flags.csv is currently empty. The quality-detection rules are not implemented yet.")
    print("Next: review the printed columns and decide on evidence-based checks for the actual data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
