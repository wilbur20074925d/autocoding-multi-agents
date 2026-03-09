"""
Rule-based autocoding pipeline.

Runs: signal_extractor → label_coder → boundary_critic → (label_coder revision) → adjudicator.
Returns structured output for the Discord dispatcher. Uses keyword/heuristic rules
when no LLM runtime is available; replace with OpenClaw/LLM calls for production.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

# Default taxonomy path relative to cloudbot
_TAXONOMY_PATH = Path(__file__).resolve().parent.parent / "data" / "label-taxonomy.csv"


def _load_taxonomy(path: Path | None = None) -> list[dict[str, str]]:
    path = path or _TAXONOMY_PATH
    rows: list[dict[str, str]] = []
    if not path.exists():
        return rows
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tier1 = (row.get("tier1") or "").strip()
            tier2 = (row.get("tier2") or "").strip()
            tier3 = (row.get("tier3") or "").strip()
            if tier1:
                label = f"{tier1}.{tier2}.{tier3}" if tier3 else f"{tier1}.{tier2}"
                rows.append({"label": label, "tier1": tier1, "tier2": tier2, "tier3": tier3, **row})
    return rows


# Heuristic keywords → candidate tier1.tier2 labels (subset of taxonomy)
_METACOGNITIVE_MONITORING = [
    "Metacognitive.monitoring.ask",
    "Metacognitive.monitoring.answer",
    "Metacognitive.monitoring.give",
]
_METACOGNITIVE_PLANNING = [
    "Metacognitive.planning.ask",
    "Metacognitive.planning.give",
]
_METACOGNITIVE_EVALUATING = [
    "Metacognitive.evaluating.ask",
    "Metacognitive.evaluating.give",
]
_COGNITIVE_CONCEPT = [
    "Cognitive.concept_exploration.ask",
    "Cognitive.concept_exploration.give",
]
_COGNITIVE_SOLUTION = [
    "Cognitive.solution_development.ask",
    "Cognitive.solution_development.give",
]
_COORDINATIVE = [
    "Coordinative.coordinate_participants.ask",
    "Coordinative.coordinate_procedures.ask",
]


def _keyword_candidates(text: str) -> list[str]:
    t = text.lower()
    candidates: list[str] = []
    if any(w in t for w in ("check", "reasoning", "correct", "progress", "track", "on the right")):
        candidates.extend(_METACOGNITIVE_MONITORING)
    if any(w in t for w in ("plan", "how should we", "solve", "procedure")):
        candidates.extend(_METACOGNITIVE_PLANNING)
    if any(w in t for w in ("evaluat", "solution", "think this", "ok", "great")):
        candidates.extend(_METACOGNITIVE_EVALUATING)
    if any(w in t for w in ("concept", "bloom", "what is", "clarif")):
        candidates.extend(_COGNITIVE_CONCEPT)
    if any(w in t for w in ("develop", "solution", "option")):
        candidates.extend(_COGNITIVE_SOLUTION)
    if any(w in t for w in ("who should", "task", "share", "allocate")):
        candidates.extend(_COORDINATIVE)
    if not candidates:
        candidates.extend(_METACOGNITIVE_MONITORING[:1])  # default one candidate
    return list(dict.fromkeys(candidates))


def _pick_best_label(candidates: list[str], text: str) -> str:
    t = text.lower()
    if any(w in t for w in ("check", "reasoning", "correct")):
        for c in candidates:
            if "monitoring" in c:
                return c
    if any(w in t for w in ("plan", "how should")):
        for c in candidates:
            if "planning" in c:
                return c
    return candidates[0] if candidates else "Metacognitive.monitoring.give"


def run_autocoding_pipeline(
    prompt: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Run the full autocoding pipeline and return structured output for the dispatcher.

    Cleans the prompt (strips Discord mentions and "Label this prompt:"), then
    runs rule-based signal extraction → label coding → boundary critic → adjudicator.
    """
    # Clean prompt: strip @mention and "Label this prompt:" prefix
    cleaned = prompt.strip()
    cleaned = re.sub(r"<@!?\d+>\s*", "", cleaned)
    prefix = "label this prompt:"
    if cleaned.lower().startswith(prefix):
        cleaned = cleaned[len(prefix) :].strip()
    if not cleaned:
        cleaned = prompt

    context = context or {}
    taxonomy = _load_taxonomy()

    # --- Signal Extractor ---
    full_span = cleaned
    start = 0
    end = len(full_span)
    evidence_spans = [{"span": full_span, "start": start, "end": end}]
    candidates = _keyword_candidates(cleaned)
    candidate_signals = [{"span_ref": 0, "candidates": candidates}]
    ambiguity: list[dict[str, Any]] = []
    if len(candidates) > 2:
        ambiguity.append({"span_ref": 0, "reason": "multiple categories plausible; could be cognitive or metacognitive"})

    signal_extractor = {
        "evidence_spans": evidence_spans,
        "candidate_signals": candidate_signals,
        "ambiguity": ambiguity,
    }

    # --- Label Coder (draft) ---
    chosen = _pick_best_label(candidates, cleaned)
    label_coder = {
        "labels": [
            {
                "span_ref": 0,
                "label": chosen,
                "evidence_used": full_span[:80] + ("..." if len(full_span) > 80 else ""),
                "rationale": "Evidence span supports this tier from taxonomy.",
            }
        ],
        "uncertain": [] if len(candidates) <= 2 else ["Boundary with other candidate(s) possible."],
        "revision_note": None,
    }

    # --- Boundary Critic ---
    challenges: list[dict[str, Any]] = []
    if "reasoning" in cleaned.lower() and "Metacognitive" in chosen:
        challenges.append({
            "assigned_label": chosen,
            "question": "Is this monitoring or evaluating?",
            "reason": "Checking reasoning can be either progress monitoring or solution evaluation.",
        })
    boundary_critic = {
        "challenges": challenges,
        "request_missing_evidence": [],
    }

    # --- Label Coder revision (if challenged) ---
    if challenges:
        label_coder["revision_note"] = "Keeping as monitoring: speaker is checking correctness of reasoning (process), not evaluating a final solution."
    else:
        pass  # keep draft as is

    # --- Adjudicator ---
    final_labels = [
        {
            "span_ref": 0,
            "label": label_coder["labels"][0]["label"],
            "decision": "accept_coder",
            "rationale": "Evidence supports assigned label; no change after review.",
        }
    ]
    adjudicator = {
        "final_labels": final_labels,
        "uncertain": label_coder.get("uncertain", []),
        "retry": None,
    }

    return {
        "prompt": cleaned,
        "context": context,
        "signal_extractor": signal_extractor,
        "label_coder": label_coder,
        "boundary_critic": boundary_critic,
        "adjudicator": adjudicator,
    }
