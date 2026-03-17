from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from cloudbot.data.training.load_training_csv import load_training_csv
from cloudbot.eval.compare import compare_one, Comparison
from cloudbot.eval.normalize import normalize_human_labels
from cloudbot.eval.reflection import ReflectionItem, reflect_mismatch, render_suggested_updates_md
from cloudbot.eval.taxonomy import build_tier2_to_tier1, load_taxonomy_rows
from cloudbot.pipeline.run_pipeline import run_autocoding_pipeline


def extract_predicted_label(pipeline_output: dict[str, Any]) -> str | None:
    adj = (pipeline_output or {}).get("adjudicator") or {}
    finals = adj.get("final_labels") or []
    if not finals:
        return None
    first = finals[0] or {}
    return first.get("label")


def run_reflection_suggestions(
    *,
    training_csv_path: str | Path,
    taxonomy_csv_path: str | Path,
    golden_strategy: str = "union",
    limit: int = 0,
) -> tuple[list[ReflectionItem], list[str]]:
    taxonomy_rows = load_taxonomy_rows(taxonomy_csv_path)
    tier2_to_tier1 = build_tier2_to_tier1(taxonomy_rows)

    examples = load_training_csv(training_csv_path)
    if limit and limit > 0:
        examples = examples[:limit]

    mismatches: list[ReflectionItem] = []
    normalization_warnings: list[str] = []

    for ex in examples:
        prompt = ex.get("prompt") or ""
        if not prompt.strip():
            continue

        # Normalize golden labels
        hc1 = ex.get("hc1") or []
        hc2 = ex.get("hc2") or []
        p1, w1 = normalize_human_labels(list(hc1), tier2_to_tier1=tier2_to_tier1)
        p2, w2 = normalize_human_labels(list(hc2), tier2_to_tier1=tier2_to_tier1)
        normalization_warnings.extend(w1 + w2)

        if golden_strategy == "hc1":
            golden_patterns = p1
        elif golden_strategy == "hc2":
            golden_patterns = p2
        elif golden_strategy == "intersection":
            s1 = {(p.tier1, p.tier2, p.tier3) for p in p1}
            s2 = {(p.tier1, p.tier2, p.tier3) for p in p2}
            keep = s1 & s2
            golden_patterns = [p for p in p1 if (p.tier1, p.tier2, p.tier3) in keep]
        else:
            # union
            seen: set[tuple[str, str | None, str | None]] = set()
            golden_patterns = []
            for p in (p1 + p2):
                k = (p.tier1, p.tier2, p.tier3)
                if k in seen:
                    continue
                seen.add(k)
                golden_patterns.append(p)

        if not golden_patterns:
            continue

        pipeline_out = run_autocoding_pipeline(
            prompt,
            context={k: ex.get(k) for k in ("group", "timestamp-mm", "people", "context") if ex.get(k)},
        )
        predicted = extract_predicted_label(pipeline_out)
        comp = compare_one(predicted, golden_patterns)
        if comp.is_match:
            continue

        mismatches.append(
            reflect_mismatch(
                prompt=prompt,
                comparison=comp,
                context_metadata={k: ex.get(k) for k in ("group", "timestamp-mm", "people", "context") if ex.get(k)},
            )
        )

    # Deduplicate warnings
    normalization_warnings = sorted(set(normalization_warnings))
    return mismatches, normalization_warnings


def write_outputs(
    *,
    repo_root: Path,
    mismatches: list[ReflectionItem],
    normalization_warnings: list[str],
    out_md: Path,
    out_jsonl: Path,
    out_warnings: Path,
) -> None:
    out_md.write_text(render_suggested_updates_md(mismatches, repo_root=repo_root), encoding="utf-8")
    with open(out_jsonl, "w", encoding="utf-8") as f:
        for it in mismatches:
            f.write(json.dumps(asdict(it), ensure_ascii=False) + "\n")
    if normalization_warnings:
        out_warnings.write_text("\n".join(normalization_warnings) + "\n", encoding="utf-8")
    else:
        # Keep file for Discord upload convenience
        out_warnings.write_text("", encoding="utf-8")

