#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import argparse
from pathlib import Path

# Allow running as a script: `python cloudbot/eval/run_reflection_suggestions.py`
if __package__ is None:  # pragma: no cover
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, os.fspath(repo_root))

from cloudbot.eval.driver import run_reflection_suggestions, write_outputs


def _repo_root_from_here() -> Path:
    # cloudbot/eval/run_reflection_suggestions.py -> repo root = ../../..
    return Path(__file__).resolve().parents[2]

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run pipeline on human-labeled training CSV and generate suggested skill updates for mismatches."
    )
    parser.add_argument(
        "--training-csv",
        default=str(Path(__file__).resolve().parents[1] / "data" / "training" / "training.csv"),
        help="Path to training CSV with HC1/HC2 columns.",
    )
    parser.add_argument(
        "--taxonomy-csv",
        default=str(Path(__file__).resolve().parents[1] / "data" / "label-taxonomy.csv"),
        help="Path to label-taxonomy.csv",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max number of rows to process (0 = all).",
    )
    parser.add_argument(
        "--golden-strategy",
        choices=["union", "hc1", "hc2", "intersection"],
        default="union",
        help="How to combine HC1 and HC2 into the golden set for comparison.",
    )
    parser.add_argument(
        "--out-md",
        default="suggested_skill_updates.md",
        help="Output markdown file (review required).",
    )
    parser.add_argument(
        "--out-jsonl",
        default="reflection_log.jsonl",
        help="Output JSONL file with structured reflection items.",
    )
    args = parser.parse_args()

    repo_root = _repo_root_from_here()
    mismatches, normalization_warnings = run_reflection_suggestions(
        training_csv_path=args.training_csv,
        taxonomy_csv_path=args.taxonomy_csv,
        golden_strategy=args.golden_strategy,
        limit=args.limit,
    )

    write_outputs(
        repo_root=repo_root,
        mismatches=mismatches,
        normalization_warnings=normalization_warnings,
        out_md=Path(args.out_md),
        out_jsonl=Path(args.out_jsonl),
        out_warnings=Path("normalization_warnings.txt"),
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

