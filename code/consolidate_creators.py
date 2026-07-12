"""
Consolidate creator lists from all Excel files in the "creators" folder.

Reads the sheet named "LIST_CREATOR" from every .xlsx file in ./creators,
adds a "source_file" column populated with each file's name, stacks all
rows together, deduplicates on the "Handle" column, and writes the result
to "all_creators.xlsx".

Usage:
    python consolidate_creators.py

Requires: pandas, openpyxl  (pip install pandas openpyxl)
"""

import sys
from pathlib import Path

import pandas as pd

SHEET_NAME = "LIST_CREATOR"
INPUT_FOLDER = "creators"
OUTPUT_FILE = "all_creators.xlsx"
DEDUPE_COLUMN = "Handle"


def find_excel_files(folder: str) -> list[Path]:
    """List all .xlsx files in the input folder."""
    p = Path(folder)
    if not p.is_dir():
        raise SystemExit(f"Input folder not found: {p.resolve()}")
    # Skip Excel's own temp lock files (~$file.xlsx)
    return sorted(f for f in p.glob("*.xlsx") if not f.name.startswith("~$"))


def read_and_tag(filepath: Path) -> pd.DataFrame | None:
    """Read the LIST_CREATOR sheet from one file and tag rows with the filename."""
    try:
        df = pd.read_excel(filepath, sheet_name=SHEET_NAME, dtype=str)
    except ValueError as e:
        # Raised by pandas when the sheet name doesn't exist in the workbook
        print(f"⚠️  '{SHEET_NAME}' sheet not found in {filepath.name} — skipping ({e})", file=sys.stderr)
        return None
    except Exception as e:
        print(f"⚠️  Could not read {filepath.name}: {e}", file=sys.stderr)
        return None

    df["source_file"] = filepath.name
    return df


def consolidate(folder: str) -> pd.DataFrame:
    files = find_excel_files(folder)
    if not files:
        raise SystemExit(f"No .xlsx files found in '{folder}/'.")

    print(f"Found {len(files)} Excel file(s) in '{folder}/'. Reading '{SHEET_NAME}' from each...")

    frames = []
    for f in files:
        df = read_and_tag(f)
        if df is not None:
            print(f"  ✓ {f.name}: {len(df)} rows")
            frames.append(df)

    if not frames:
        raise SystemExit(f"No '{SHEET_NAME}' sheet was successfully read from any file.")

    consolidated = pd.concat(frames, ignore_index=True)
    print(f"\nConsolidated total (before dedup): {len(consolidated)} rows from {len(frames)} file(s).")
    return consolidated


def dedupe(df: pd.DataFrame, column: str) -> pd.DataFrame:
    """Deduplicate on `column`, keeping the first occurrence. Warns if the column is missing."""
    if column not in df.columns:
        print(
            f"⚠️  Column '{column}' not found — available columns: {list(df.columns)}. "
            "Skipping deduplication.",
            file=sys.stderr,
        )
        return df

    before = len(df)
    # Normalize for comparison (strip whitespace, ignore case) without altering the stored value
    key = df[column].astype(str).str.strip().str.lower()
    deduped = df.loc[~key.duplicated(keep="first")].reset_index(drop=True)
    removed = before - len(deduped)
    print(f"Deduplicated on '{column}': removed {removed} duplicate row(s), {len(deduped)} remain.")
    return deduped


def main():
    consolidated = consolidate(INPUT_FOLDER)
    deduped = dedupe(consolidated, DEDUPE_COLUMN)

    out_path = Path(OUTPUT_FILE)
    deduped.to_excel(out_path, index=False, sheet_name=SHEET_NAME)

    print(f"\nSaved deduplicated file to: {out_path.resolve()}")


if __name__ == "__main__":
    main()
