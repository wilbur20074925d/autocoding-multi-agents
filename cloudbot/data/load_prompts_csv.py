#!/usr/bin/env python3
"""
Load a CSV file that contains only user prompts (one prompt per row).

Supports:
- CSV with header: use column named "prompt" or "sentence", or the first column.
- CSV without header: use the first column as prompt.
- Empty rows are skipped.
- Prompts that contain commas should be quoted in the CSV so they are read as one cell.

Usage:
  python load_prompts_csv.py path/to/prompts.csv
  python load_prompts_csv.py path/to/prompts.csv --output prompts.jsonl

When the AI processes a prompts-only CSV, it runs the full autocoding pipeline
on each prompt one by one; the next prompt does not start until the previous
one is complete.
"""

import csv
import argparse
import json
import sys
from pathlib import Path


def load_prompts_csv(path: str | Path) -> list[str]:
    """
    Load a CSV that contains only prompts (one per row).

    Returns a list of non-empty prompt strings in order.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")

    prompts: list[str] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        row0 = next(reader, None)
        if row0 is None:
            return prompts

        first_cell = (row0[0].strip() if row0 else "")
        # Treat first row as header only if it's exactly a known column name
        header_names = ("prompt", "sentence", "text", "utterance")
        has_header = bool(first_cell and first_cell.lower() in header_names)

        if has_header:
            header = [h.strip().lower() for h in row0]
            col_map = {h: i for i, h in enumerate(header)}
            prompt_col = next(
                (col_map[k] for k in ("prompt", "sentence", "text", "utterance") if k in col_map),
                0,
            )
            for row in reader:
                if prompt_col < len(row):
                    p = row[prompt_col].strip()
                    if p:
                        prompts.append(p)
        else:
            # No header: first column is prompt for every row
            if first_cell:
                prompts.append(first_cell)
            for row in reader:
                if row and row[0].strip():
                    prompts.append(row[0].strip())

    return prompts


def main():
    parser = argparse.ArgumentParser(
        description="Load a CSV of user prompts (one per row) for the autocoding pipeline."
    )
    parser.add_argument(
        "csv_path",
        nargs="?",
        help="Path to prompts-only CSV",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="FILE",
        help="Write one JSON object per line: {\"prompt\": \"...\"}",
    )
    args = parser.parse_args()

    if not args.csv_path:
        print("Usage: python load_prompts_csv.py <path/to/prompts.csv>", file=sys.stderr)
        sys.exit(1)

    try:
        prompts = load_prompts_csv(args.csv_path)
    except Exception as e:
        print(f"Error loading CSV: {e}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        out_path = Path(args.output)
        with open(out_path, "w", encoding="utf-8") as f:
            for p in prompts:
                f.write(json.dumps({"prompt": p}, ensure_ascii=False) + "\n")
        print(f"Wrote {len(prompts)} prompts to {out_path}", file=sys.stderr)
    else:
        for i, p in enumerate(prompts):
            print(json.dumps({"index": i + 1, "prompt": p}, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
