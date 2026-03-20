"""
Rule-based autocoding pipeline.

Runs: signal_extractor → label_coder → boundary_critic → (label_coder revision) → adjudicator.
Returns structured output for the Discord dispatcher. Uses keyword/heuristic rules
when no LLM runtime is available; replace with OpenClaw/LLM calls for production.
"""

from __future__ import annotations

import csv
import os
import re
from pathlib import Path
from typing import Any

from cloudbot.llm.openai_compat import chat_completions_json, load_config_from_env

# Default taxonomy path relative to cloudbot
_TAXONOMY_PATH = Path(__file__).resolve().parent.parent / "data" / "label-taxonomy.csv"
_GOLDEN_LABELS_PATH = Path(__file__).resolve().parent.parent / "data" / "golden-labels.md"
_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "system.md"


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


_CACHED_TAXONOMY_CODES: list[str] | None = None
_CACHED_GOLDEN_SUMMARY: str | None = None
_CACHED_SYSTEM_PROMPT: str | None = None


def _taxonomy_codes() -> list[str]:
    global _CACHED_TAXONOMY_CODES
    if _CACHED_TAXONOMY_CODES is not None:
        return _CACHED_TAXONOMY_CODES
    rows = _load_taxonomy()
    _CACHED_TAXONOMY_CODES = sorted({r["label"] for r in rows if r.get("label")})
    return _CACHED_TAXONOMY_CODES


def _golden_summary() -> str:
    """
    Keep this short: boundaries + tier3 action rules are the highest-signal bits.
    """
    global _CACHED_GOLDEN_SUMMARY
    if _CACHED_GOLDEN_SUMMARY is not None:
        return _CACHED_GOLDEN_SUMMARY
    if not _GOLDEN_LABELS_PATH.exists():
        _CACHED_GOLDEN_SUMMARY = ""
        return _CACHED_GOLDEN_SUMMARY
    txt = _GOLDEN_LABELS_PATH.read_text(encoding="utf-8")
    # Extract just the Tier1 boundary table + decision rules section if present.
    # Fallback: first ~180 lines.
    lines = txt.splitlines()
    keep: list[str] = []
    for i, line in enumerate(lines[:220]):
        keep.append(line)
    _CACHED_GOLDEN_SUMMARY = "\n".join(keep).strip()
    return _CACHED_GOLDEN_SUMMARY


def _system_prompt() -> str:
    global _CACHED_SYSTEM_PROMPT
    if _CACHED_SYSTEM_PROMPT is not None:
        return _CACHED_SYSTEM_PROMPT
    if not _SYSTEM_PROMPT_PATH.exists():
        _CACHED_SYSTEM_PROMPT = ""
        return _CACHED_SYSTEM_PROMPT
    _CACHED_SYSTEM_PROMPT = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    return _CACHED_SYSTEM_PROMPT


def _llm_enabled() -> bool:
    # Explicit off switch if needed.
    if (os.environ.get("CLOUDBOT_LLM_DISABLED") or "").strip() in ("1", "true", "yes", "on"):
        return False
    return load_config_from_env() is not None


def _run_llm_pipeline(cleaned_prompt: str, context: dict[str, Any]) -> dict[str, Any] | None:
    cfg = load_config_from_env()
    if cfg is None:
        return None

    allowed = _taxonomy_codes()
    golden = _golden_summary()
    sys_prompt = _system_prompt()

    # Keep output schema aligned with Discord dispatcher expectations.
    instruction = f"""
You will simulate the 4-agent autocoding pipeline and output ONE JSON object with this exact structure:
{{
  "prompt": <string>,
  "context": <object>,
  "signal_extractor": {{
    "evidence_spans": [{{"span": <string>, "start": <int>, "end": <int>, "reason": <string>}}],
    "candidate_signals": [{{"span_ref": <int>, "candidates": <array of strings>, "reason": <string>}}],
    "ambiguity": [{{"span_ref": <int>, "reason": <string>}}]
  }},
  "label_coder": {{
    "labels": [{{"span_ref": <int>, "label": <string>, "evidence_used": <string>, "rationale": <string>}}],
    "uncertain": <array>,
    "revision_note": <string|null>
  }},
  "boundary_critic": {{
    "challenges": <array>,
    "request_missing_evidence": <array>
  }},
  "adjudicator": {{
    "final_labels": [{{"span_ref": <int>, "label": <string>, "decision": <string>, "rationale": <string>}}],
    "uncertain": <array>,
    "retry": <object|null>
  }}
}}

Rules:
- All four agents start with EMPTY memory for this run. Do not use any prior conversation state.
- Use ONLY the current prompt/context and provided artifacts (taxonomy + golden labels).
- Final labels MUST be chosen from this allowed taxonomy list:
{allowed}
- Use golden-labels boundaries and decision rules below.
- Keep evidence spans minimal but sufficient; use span_ref=0 for the whole prompt if needed.
- Boundary Critic must only challenge, not decide.
- Adjudicator must decide (accept_coder / accept_critic / combine / uncertain) and justify.

Golden-labels criteria excerpt:
{golden}
""".strip()

    messages = [
        {"role": "system", "content": sys_prompt or "You are an autocoding pipeline."},
        {"role": "user", "content": f"Context: {context}\n\nPrompt:\n{cleaned_prompt}\n\n{instruction}"},
    ]

    try:
        out = chat_completions_json(cfg=cfg, messages=messages, temperature=0.1, max_tokens=1800)
    except Exception:
        return None

    # Minimal validation: ensure required top-level keys exist.
    if not isinstance(out, dict):
        return None
    for k in ("signal_extractor", "label_coder", "boundary_critic", "adjudicator"):
        if k not in out:
            return None
    out.setdefault("prompt", cleaned_prompt)
    out.setdefault("context", context)
    return out


# Heuristic keywords → candidate tier1.tier2 labels (subset of taxonomy)
_METACOGNITIVE_MONITORING = ["Metacognitive.monitoring"]
_METACOGNITIVE_PLANNING = ["Metacognitive.planning"]
_METACOGNITIVE_EVALUATING = ["Metacognitive.evaluating"]
_COGNITIVE_CONCEPT = ["Cognitive.concept_exploration"]
_COGNITIVE_SOLUTION = ["Cognitive.solution_development"]
_COORDINATIVE = ["Coordinative.coordinate_participants", "Coordinative.coordinate_procedures"]


def _keyword_candidates(text: str) -> list[str]:
    t = text.lower()
    candidates: list[str] = []
    if any(w in t for w in ("progress", "track", "on the right track", "move to next", "next question", "speed up")):
        candidates.extend(_METACOGNITIVE_MONITORING)
    if any(w in t for w in ("plan", "how should we", "what steps", "procedure", "first we", "strategy")):
        candidates.extend(_METACOGNITIVE_PLANNING)
    if any(w in t for w in ("evaluat", "is this", "good enough", "correct", "does this make sense", "quality")):
        candidates.extend(_METACOGNITIVE_EVALUATING)
    if any(w in t for w in ("concept", "bloom", "what is", "clarif", "meaning", "define")):
        candidates.extend(_COGNITIVE_CONCEPT)
    if any(w in t for w in ("answer", "option", "solution should", "final answer")):
        candidates.extend(_COGNITIVE_SOLUTION)
    if any(w in t for w in ("who should", "divide", "split", "allocate", "you go first", "turn", "share")):
        candidates.extend(_COORDINATIVE)
    if not candidates:
        # Safer fallback: avoid over-predicting monitoring when intent is unclear.
        candidates.extend(_COGNITIVE_CONCEPT)
    return list(dict.fromkeys(candidates))


def _pick_best_label(candidates: list[str], text: str) -> str:
    t = text.lower()
    if any(w in t for w in ("progress", "track", "next question", "speed up")):
        for c in candidates:
            if c == "Metacognitive.monitoring":
                return c
    if any(w in t for w in ("plan", "how should", "what steps", "strategy", "first we")):
        for c in candidates:
            if c == "Metacognitive.planning":
                return c
    if any(w in t for w in ("is this", "good enough", "does this make sense", "correct")):
        for c in candidates:
            if c == "Metacognitive.evaluating":
                return c
    if any(w in t for w in ("what is", "define", "meaning", "concept", "bloom")):
        for c in candidates:
            if c == "Cognitive.concept_exploration":
                return c
    if any(w in t for w in ("answer", "option", "final answer")):
        for c in candidates:
            if c == "Cognitive.solution_development":
                return c
    return candidates[0] if candidates else "Cognitive.concept_exploration"


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

    # Prefer LLM-backed pipeline when configured; fallback to heuristics.
    if _llm_enabled():
        llm_out = _run_llm_pipeline(cleaned, context)
        if llm_out is not None:
            return llm_out

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
