"""
Chunk all_creators.xlsx into template files of at most 100 handles each.

Rules:
  - Rows are grouped by "source_file" first — a chunk never mixes rows
    from two different source files (so a chunk can be smaller than 100).
  - Each chunk is written into a copy of creator_template.xlsx, keeping
    the template's first row and appending the chunk's Handle values
    starting from row 2.
  - Output filename: "{source_file without .xlsx}_{chunk_number:02d}.xlsx"
    e.g. Kalodata_All_00k-02k_20260710_01.xlsx, ..._02.xlsx, etc.

Usage:
    python chunk_creators.py

Requires: pandas, openpyxl  (pip install pandas openpyxl)
"""

from pathlib import Path

import openpyxl
import pandas as pd

ALL_CREATORS_FILE = "all_creators.xlsx"
TEMPLATE_FILE = "creator_template.xlsx"
OUTPUT_FOLDER = "chunked_output"
CHUNK_SIZE = 100


def chunk_list(items: list, size: int) -> list[list]:
    """Split a list into consecutive chunks of at most `size` items."""
    return [items[i:i + size] for i in range(0, len(items), size)]


def write_chunk_file(handles: list[str], output_path: Path):
    """Copy the template and append handles starting at row 2 (row 1 is kept as-is)."""
    wb = openpyxl.load_workbook(TEMPLATE_FILE)
    ws = wb.active
    for i, handle in enumerate(handles, start=2):
        ws.cell(row=i, column=1, value=handle)
    wb.save(output_path)


def main():
    df = pd.read_excel(ALL_CREATORS_FILE, dtype=str)

    out_folder = Path(OUTPUT_FOLDER)
    out_folder.mkdir(exist_ok=True)

    for source_file, group in df.groupby("source_file"):
        handles = group["Handle"].tolist()
        chunks = chunk_list(handles, CHUNK_SIZE)

        base_name = Path(source_file).stem  # strips .xlsx
        for i, chunk in enumerate(chunks, start=1):
            output_path = out_folder / f"{base_name}_{i:02d}.xlsx"
            write_chunk_file(chunk, output_path)
            print(f"Wrote {len(chunk)} handles to {output_path}")


if __name__ == "__main__":
    main()
