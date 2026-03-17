from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from cloudbot.eval.compare import Comparison
from cloudbot.eval.labels import LabelPattern, ParsedCode, parse_predicted_code


@dataclass(frozen=True)
class ReflectionItem:
    prompt: str
    predicted: str | None
    golden: list[str]
    mismatch_type: str
    reason: str
    target_skill_files: list[str]
    suggested_text_blocks: list[str]
    few_shot_candidates: list[dict[str, Any]]
    metadata: dict[str, Any]


def _pattern_to_string(p: LabelPattern) -> str:
    if p.tier2 is None:
        return p.tier1
    if p.tier3 is None:
        return f"{p.tier1}.{p.tier2}"
    return f"{p.tier1}.{p.tier2}.{p.tier3}"


def _golden_strings(patterns: list[LabelPattern]) -> list[str]:
    # de-dupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for p in patterns:
        s = _pattern_to_string(p)
        if s not in seen:
            out.append(s)
            seen.add(s)
    return out


def _choose_reference_golden(patterns: list[LabelPattern]) -> LabelPattern | None:
    if not patterns:
        return None
    return sorted(patterns, key=lambda x: x.specificity(), reverse=True)[0]


def _explain_mismatch(pred: ParsedCode | None, ref: LabelPattern | None, mismatch_type: str) -> str:
    if mismatch_type == "no_prediction":
        return "Pipeline produced no prediction; add a fallback rule or mark uncertain rather than defaulting silently."
    if mismatch_type == "no_golden":
        return "No golden label available for this prompt; reflection is not actionable."
    if mismatch_type == "invalid_predicted_format":
        return "Predicted label is not in Tier1.tier2(.tier3) format; enforce taxonomy formatting."
    if pred is None or ref is None:
        return "Mismatch; insufficient detail to explain."

    if mismatch_type == "tier1":
        return (
            f"Tier1 boundary mismatch: predicted **{pred.tier1}** but golden expects **{ref.tier1}**. "
            "This usually means confusing *content (Cognitive)* vs *process (Metacognitive)*, "
            "*coordination (Coordinative)* vs *affect (Socio-emotional)*. Add an edge-case reminder + example."
        )
    if mismatch_type == "tier2":
        return (
            f"Tier2 mismatch under tier1={pred.tier1}: predicted **{pred.tier2}** but golden expects **{ref.tier2}**. "
            "Add a tighter decision rule and a concrete example utterance in the relevant SKILL."
        )
    if mismatch_type == "tier3":
        return (
            f"Tier3 (speech act) mismatch: predicted **{pred.tier3}** but golden expects **{ref.tier3}**. "
            "Common boundary errors: agree vs build_on, ask vs give. Add a checklist item + minimal pairs."
        )
    return "Mismatch type 'other': add an example and clarify the boundary rule in skills."


def _targets_for_mismatch(mismatch_type: str) -> list[str]:
    # Keep this conservative: suggest edits only to SKILL files, not taxonomy/golden-labels.
    base = [
        ".cursor/skills/label-coder/SKILL.md",
        ".cursor/skills/boundary-critic/SKILL.md",
    ]
    if mismatch_type in ("invalid_predicted_format", "no_prediction"):
        base.append(".cursor/skills/adjudicator/SKILL.md")
    return base


def _suggested_blocks(
    *,
    prompt: str,
    predicted: str | None,
    golden_ref: LabelPattern | None,
    mismatch_type: str,
) -> list[str]:
    # Blocks are designed to be pasted into SKILL.md under "Accuracy" or "Edge cases".
    gold = _pattern_to_string(golden_ref) if golden_ref else "(unknown)"
    pred = predicted or "(none)"

    if mismatch_type == "tier1":
        return [
            "Add under **Accuracy** (Label Coder + Boundary Critic):\n\n"
            f'- **Tier1 boundary reminder (with example)**: Prompt `{prompt}` was predicted as `{pred}` but golden was `{gold}`. '
            "Re-check *content vs process* (Cognitive vs Metacognitive), *coordination vs affect* (Coordinative vs Socio-emotional) "
            "using `cloudbot/data/golden-labels.md` Tier1 table before committing.\n"
        ]
    if mismatch_type == "tier2":
        return [
            "Add under **Accuracy** (Label Coder + Boundary Critic):\n\n"
            f'- **Tier2 decision rule (with example)**: Prompt `{prompt}` predicted `{pred}` but golden `{gold}`. '
            "Write a one-line rule: when it’s about X → tier2=A; when about Y → tier2=B.\n"
        ]
    if mismatch_type == "tier3":
        return [
            "Add under **Accuracy** (Label Coder + Boundary Critic):\n\n"
            f'- **Tier3 speech-act check (with example)**: Prompt `{prompt}` predicted `{pred}` but golden `{gold}`. '
            "Before output, explicitly justify ask/answer/give/agree/build_on/disagree; default to **agree** unless there is clear new content for **build_on**.\n"
        ]
    if mismatch_type == "no_prediction":
        return [
            "Add under **Output requirements / robustness** (Adjudicator or Label Coder):\n\n"
            f'- **No empty predictions**: For prompt `{prompt}`, pipeline produced no label. If evidence exists but classification is unclear, output `uncertain` with candidate set rather than defaulting.\n'
        ]
    if mismatch_type == "invalid_predicted_format":
        return [
            "Add under **Taxonomy enforcement** (Label Coder + Adjudicator):\n\n"
            f'- **Taxonomy format**: Predicted `{pred}` is not a valid taxonomy code. Always output `Tier1.tier2.tier3` (or `Socio-emotional.tier2`) from `cloudbot/data/label-taxonomy.csv`.\n'
        ]
    return [
        "Add under **Edge cases** (Label Coder + Boundary Critic):\n\n"
        f'- **Worked example**: Prompt `{prompt}` predicted `{pred}` but golden `{gold}`. Include as a boundary example.\n'
    ]


def _few_shot_candidates(
    *,
    prompt: str,
    predicted: str | None,
    golden_ref: LabelPattern | None,
    mismatch_type: str,
) -> list[dict[str, Any]]:
    """
    Few-shot entries are meant to be copy/pasted into a future SKILL section or
    used as runtime context examples.
    """
    gold = _pattern_to_string(golden_ref) if golden_ref else None
    if not gold:
        return []
    rule = {
        "tier1": "Use golden-labels.md Tier1 table: content=Cognitive, process=Metacognitive, coordination=Coordinative, affect=Socio-emotional.",
        "tier2": "Use golden-labels.md decision rules for tier2 within the chosen tier1.",
        "tier3": "Use golden-labels.md tier3 rules: ask vs give; agree vs build_on (build_on requires clear extension).",
    }.get(mismatch_type, "Apply golden-labels.md accuracy checklist before finalizing.")
    return [
        {
            "prompt": prompt,
            "golden_label": gold,
            "model_wrong_label": predicted,
            "mismatch_type": mismatch_type,
            "rule_of_thumb": rule,
        }
    ]


def reflect_mismatch(
    *,
    prompt: str,
    comparison: Comparison,
    context_metadata: dict[str, Any] | None = None,
) -> ReflectionItem:
    context_metadata = context_metadata or {}
    pred_parsed = parse_predicted_code(comparison.predicted or "")
    ref = _choose_reference_golden(comparison.golden_patterns)

    mismatch_type = comparison.mismatch_type or "other"
    reason = _explain_mismatch(pred_parsed, ref, mismatch_type)
    targets = _targets_for_mismatch(mismatch_type)
    blocks = _suggested_blocks(prompt=prompt, predicted=comparison.predicted, golden_ref=ref, mismatch_type=mismatch_type)
    few_shots = _few_shot_candidates(prompt=prompt, predicted=comparison.predicted, golden_ref=ref, mismatch_type=mismatch_type)

    meta = dict(context_metadata)
    meta["generated_at"] = datetime.utcnow().isoformat() + "Z"

    return ReflectionItem(
        prompt=prompt,
        predicted=comparison.predicted,
        golden=_golden_strings(comparison.golden_patterns),
        mismatch_type=mismatch_type,
        reason=reason,
        target_skill_files=targets,
        suggested_text_blocks=blocks,
        few_shot_candidates=few_shots,
        metadata=meta,
    )


def render_suggested_updates_md(
    items: list[ReflectionItem],
    *,
    repo_root: Path,
) -> str:
    by_target: dict[str, list[ReflectionItem]] = {}
    for it in items:
        for t in it.target_skill_files:
            by_target.setdefault(t, []).append(it)

    total = len(items)
    now = datetime.utcnow().isoformat() + "Z"

    lines: list[str] = []
    lines.append("# Suggested skill updates (REVIEW REQUIRED)")
    lines.append("")
    lines.append(f"- Generated at: `{now}`")
    lines.append(f"- Total mismatches reflected: **{total}**")
    lines.append("- This file is **not auto-applied**; copy/paste edits after review.")
    lines.append("")

    for target, group in sorted(by_target.items(), key=lambda kv: kv[0]):
        abs_path = repo_root / target
        lines.append(f"## Target: `{target}`")
        lines.append("")
        lines.append(f"- File exists: **{abs_path.exists()}**")
        lines.append(f"- Items: **{len(group)}**")
        lines.append("")
        for idx, it in enumerate(group, start=1):
            lines.append(f"### Item {idx}")
            lines.append("")
            lines.append(f"- **Prompt**: `{it.prompt}`")
            lines.append(f"- **Predicted**: `{it.predicted}`")
            lines.append(f"- **Golden**: `{', '.join(it.golden)}`")
            lines.append(f"- **Mismatch type**: `{it.mismatch_type}`")
            lines.append(f"- **Reason**: {it.reason}")
            lines.append("")
            for block in it.suggested_text_blocks:
                lines.append("```text")
                lines.append(block.rstrip())
                lines.append("```")
                lines.append("")

            if it.few_shot_candidates:
                lines.append("- **Few-shot candidate(s)** (copy/paste into a SKILL 'Few-shot' section or runtime context):")
                lines.append("")
                lines.append("```json")
                for fs in it.few_shot_candidates:
                    lines.append(json.dumps(fs, ensure_ascii=False))
                lines.append("```")
                lines.append("")

    return "\n".join(lines).rstrip() + "\n"

