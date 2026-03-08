#!/usr/bin/env python3
"""
Load training data from the human-coded CSV format.

CSV columns: group, timestamp-mm, people, context, sentence, LLMs-revised sentence, HC1, HC2
- sentence: the utterance to label (used as prompt).
- HC1, HC2: human-coder labels (ground truth); may be comma-separated in each cell.

Usage:
  python load_training_csv.py [path/to/training.csv] [--use-revised] [--output examples.jsonl]
"""

import csv
import argparse
import json
import sys
from pathlib import Path


def _normalize_headers(row):
    return [h.strip() for h in row]


def _parse_labels(cell):
    """Parse a cell that may contain comma-separated labels. Returns list of stripped strings."""
    if not cell or not str(cell).strip():
        return []
    return [s.strip() for s in str(cell).split(",") if s.strip()]


def load_training_csv(
    path: str | Path,
    *,
    use_revised_sentence: bool = False,
    prompt_column: str | None = None,
) -> list[dict]:
    """
    Load training CSV and return list of examples.

    Each example dict has:
      - prompt: text to be labeled (from sentence or LLMs-revised sentence)
      - hc1: list of labels from Human Coder 1 (ground truth)
      - hc2: list of labels from Human Coder 2 (ground truth)
      - group, context, etc. if present in CSV
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Training CSV not found: {path}")

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = _normalize_headers(next(reader))
        # Allow flexible column names (with/without spaces)
        col_map = {h: i for i, h in enumerate(header)}

        sentence_col = prompt_column or ("LLMs-revised sentence" if use_revised_sentence else "sentence")
        if sentence_col not in col_map:
            raise ValueError(
                f"Column '{sentence_col}' not found. Available: {list(col_map.keys())}"
            )

        hc1_col = "HC1"
        hc2_col = "HC2"
        if hc1_col not in col_map or hc2_col not in col_map:
            raise ValueError(
                f"CSV must have HC1 and HC2 columns. Available: {list(col_map.keys())}"
            )

        examples = []
        for row in reader:
            if len(row) <= max(col_map[sentence_col], col_map[hc1_col], col_map[hc2_col]):
                continue
            prompt = row[col_map[sentence_col]].strip()
            if not prompt:
                continue
            hc1 = _parse_labels(row[col_map[hc1_col]])
            hc2 = _parse_labels(row[col_map[hc2_col]])

            ex = {
                "prompt": prompt,
                "hc1": hc1,
                "hc2": hc2,
            }
            for key in ("group", "timestamp-mm", "people", "context"):
                if key in col_map and col_map[key] < len(row):
                    ex[key] = row[col_map[key]].strip()
            examples.append(ex)
        return examples


def main():
    parser = argparse.ArgumentParser(
        description="Load training CSV (HC1/HC2 = human labels) and optionally export JSONL."
    )
    parser.add_argument(
        "csv_path",
        nargs="?",
        default="training.csv",
        help="Path to training CSV (default: training.csv in same directory)",
    )
    parser.add_argument(
        "--use-revised",
        action="store_true",
        help="Use 'LLMs-revised sentence' as prompt instead of 'sentence'",
    )
    parser.add_argument(
        "--output",
        "-o",
        metavar="FILE",
        help="Write one JSONL line per example (prompt, hc1, hc2) to FILE",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv_path)
    if not csv_path.is_absolute():
        csv_path = Path(__file__).resolve().parent / csv_path

    try:
        examples = load_training_csv(
            csv_path,
            use_revised_sentence=args.use_revised,
        )
    except Exception as e:
        print(f"Error loading CSV: {e}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        out_path = Path(args.output)
        with open(out_path, "w", encoding="utf-8") as f:
            for ex in examples:
                # For compatibility: include a single "label" as first HC1 label if present
                line = dict(ex)
                if ex.get("hc1"):
                    line["label"] = ex["hc1"][0]
                elif ex.get("hc2"):
                    line["label"] = ex["hc2"][0]
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        print(f"Wrote {len(examples)} examples to {out_path}", file=sys.stderr)
    else:
        json.dump(examples, sys.stdout, indent=2, ensure_ascii=False)

    return 0


if __name__ == "__main__":
    sys.exit(main())
