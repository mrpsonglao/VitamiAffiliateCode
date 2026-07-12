"""
Consolidate creator lists from all Excel files in the "creators" folder.

Reads the sheet named "LIST_CREATOR" from every .xlsx file in ./creators,
adds a "source_file" column populated with each file's name, stacks all
rows together, deduplicates on the "Handle" column (keeping the last
occurrence), and writes the result to "all_creators.xlsx".

Usage:
    python consolidate_creators.py

Requires: pandas, openpyxl  (pip install pandas openpyxl)
"""

from pathlib import Path

import pandas as pd

SHEET_NAME = "LIST_CREATOR"
INPUT_FOLDER = "../creators"
OUTPUT_FILE = "all_creators.xlsx"
DEDUPE_COLUMN = "Handle"


def load_and_tag(filepath: Path) -> pd.DataFrame:
    """Read the LIST_CREATOR sheet from one file and tag rows with the filename."""
    df = pd.read_excel(filepath, sheet_name=SHEET_NAME, dtype=str)
    df["source_file"] = filepath.name
    return df


def main():
    folder = Path(INPUT_FOLDER)
    files = sorted(f for f in folder.glob("*.xlsx") if not f.name.startswith("~$"))
    if not files:
        raise SystemExit(f"No .xlsx files found in '{folder}/'.")

    consolidated = pd.concat([load_and_tag(f) for f in files], ignore_index=True)

    key = consolidated[DEDUPE_COLUMN].astype(str).str.strip().str.lower()
    deduped = consolidated.loc[~key.duplicated(keep="last")].reset_index(drop=True)

    deduped.to_excel(OUTPUT_FILE, index=False, sheet_name=SHEET_NAME)
    print(f"Saved {len(deduped)} deduplicated rows to {Path(OUTPUT_FILE).resolve()}")


if __name__ == "__main__":
    main()