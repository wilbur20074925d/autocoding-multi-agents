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


# --- Content-based label scoring (heuristic + LLM bias repair) ---
# Maps prompt text → best Tier1.Tier2 label without defaulting to concept_exploration.

_LABEL_SCORE_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    # Socio-emotional (often short / affective — check before generic cognitive)
    ("Socio-emotional.emotional_expression", (
        "haha", "hahaha", "lol", "lmao", "omg", "hilarious", "funny",
        "frustrated", "annoyed", "nervous", "worried", "sad", "angry",
        "that's hilarious", "so funny", "哈哈", "哈哈哈", "😂", "😭", "😅",
    )),
    ("Socio-emotional.encouragement", (
        "thank you", "thanks", "thank", "good job", "great job", "nice work",
        "well done", "keep going", "don't worry", "you got this", "cheer",
        "appreciate", "grateful",
    )),
    ("Socio-emotional.self_disclosure", (
        "i've ", "i have ", "i was ", "i'm not familiar", "i am not familiar",
        "first time", "never done", "worked as", "my experience", "personally",
        "i don't know much", "not good at",
    )),
    ("Coordinative.coordinate_participants", (
        "who should", "divide the", "split the task", "allocate", "you do ",
        "i will do", "your part", "roles", "who handles", "who does",
    )),
    ("Coordinative.coordinate_procedures", (
        "you go first", "go first", "take turns", "whose turn", "order of",
        "in the chat", "google doc", "bullet", "paragraph", "where do we write",
        "share screen", "workflow",
    )),
    ("Metacognitive.planning", (
        "how should we", "what steps", "plan", "strategy", "approach",
        "first we", "let's start by", "should we begin", "procedure for solving",
        "goal is", "order of steps",
    )),
    ("Metacognitive.monitoring", (
        "on the right track", "are we", "progress", "move on", "next question",
        "speed up", "behind schedule", "on track", "pace", "time left",
    )),
    ("Metacognitive.evaluating", (
        "lack detail", "lacks detail", "good enough", "make sense",
        "is this ok", "is this correct", "evaluate", "quality", "weak",
        "strong enough", "gpt results", "does our", "think this",
    )),
    ("Cognitive.solution_development", (
        "option ", "options ", "answer is", "the answer", "final answer",
        "choose ", "pick ", "correct option", "last one", "a b c d",
        "which one is", "solution should",
    )),
    ("Cognitive.concept_exploration", (
        "what is ", "what are ", "define ", "definition", "meaning of",
        "concept of", "bloom", "taxonomy", "clarify", "explain what",
        "what does ", "metacognitive mean", "what is meant",
    )),
]


def _label_scores(text: str) -> dict[str, float]:
    """Higher score = stronger match. No label gets a free prior."""
    t = (text or "").lower()
    scores: dict[str, float] = {code: 0.0 for code in _taxonomy_codes()}
    for label, phrases in _LABEL_SCORE_PATTERNS:
        if label not in scores:
            continue
        for p in phrases:
            if p in t:
                scores[label] += 2.5 if len(p) > 4 else 1.5
    # Light boosts for question marks (often planning or concept — tie-break with context)
    if "?" in text and scores["Metacognitive.planning"] == 0 and "how " in t:
        scores["Metacognitive.planning"] += 1.0
    if "?" in text and any(x in t for x in ("what is", "what does", "define", "meaning")):
        scores["Cognitive.concept_exploration"] += 1.0
    return scores


def _best_label_from_scores(scores: dict[str, float]) -> tuple[str, float]:
    if not scores:
        return "Cognitive.solution_development", 0.0
    best = max(scores.items(), key=lambda kv: kv[1])
    return best[0], best[1]


def _infer_label_from_prompt(text: str) -> tuple[str, list[str], dict[str, float]]:
    """
    Returns (chosen_label, ranked_candidates_desc, scores).
    Picks label from content scores — does **not** default to Cognitive.concept_exploration.
    """
    scores = _label_scores(text)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    chosen, top = ranked[0][0], ranked[0][1]
    stripped = (text or "").strip().lower()
    if len(stripped) <= 20 and any(x in stripped for x in ("haha", "lol", "哈哈", "😂")):
        return "Socio-emotional.emotional_expression", [
            "Socio-emotional.emotional_expression",
            "Socio-emotional.encouragement",
        ], scores
    if top <= 0:
        # No keyword hit: infer from coarse cues (still avoid concept_exploration as blanket default)
        if any(w in stripped for w in ("how ", "should we", "let's", "we can", "next ", "plan")):
            chosen = "Metacognitive.planning"
        elif any(w in stripped for w in ("thank", "good job", "nice", "great")):
            chosen = "Socio-emotional.encouragement"
        elif any(w in stripped for w in ("split", "divide", "who ", "you do")):
            chosen = "Coordinative.coordinate_participants"
        else:
            chosen = "Cognitive.solution_development"
        candidates = [c for c, _ in ranked[:3]]
        if chosen not in candidates:
            candidates = [chosen] + [c for c in candidates if c != chosen][:2]
        return chosen, candidates, scores
    candidates = [c for c, s in ranked[:5] if s > 0][:3]
    if not candidates:
        candidates = [chosen]
    return chosen, candidates, scores


# Margin between #1 and #2 scores below this ⇒ Boundary Critic should refine boundaries.
SCORE_CLOSE_THRESHOLD = 1.5


def _enrich_label_coder_scores(
    label_coder: dict[str, Any],
    score_map: dict[str, float],
    *,
    allowed_codes: list[str],
    close_threshold: float = SCORE_CLOSE_THRESHOLD,
) -> None:
    """Attach full label_scores, ranked list, closeness flag, and Discord-ready display."""
    from cloudbot.discord.format import build_label_scores_display

    full = {code: float(score_map.get(code, 0.0)) for code in allowed_codes}
    ranked = sorted(full.items(), key=lambda kv: (-kv[1], kv[0]))
    label_coder["label_scores"] = full
    label_coder["label_scores_ranked"] = [
        {"label": k, "score": round(v, 2)} for k, v in ranked
    ]
    margin = (ranked[0][1] - ranked[1][1]) if len(ranked) > 1 else ranked[0][1]
    label_coder["scores_close"] = bool(ranked[0][1] > 0 and margin < close_threshold)
    label_coder["label_scores_margin_top2"] = round(margin, 3)
    label_coder["label_scores_display"] = build_label_scores_display(full)


def _merge_label_scores_with_heuristic(
    lc: dict[str, Any],
    cleaned_prompt: str,
    allowed_codes: list[str],
) -> dict[str, float]:
    """Ensure every taxonomy code has a score; blend LLM scores with heuristic when missing."""
    heur = _label_scores(cleaned_prompt)
    raw = lc.get("label_scores")
    if not isinstance(raw, dict):
        raw = {}
    merged: dict[str, float] = {}
    for code in allowed_codes:
        v = raw.get(code)
        if v is None:
            merged[code] = float(heur.get(code, 0.0))
        else:
            try:
                merged[code] = float(v)
            except (TypeError, ValueError):
                merged[code] = float(heur.get(code, 0.0))
    return merged


def _pick_final_label_from_ranked(
    ranked_pairs: list[tuple[str, float]],
    fallback_label: str,
) -> str:
    """Pick argmax when top score > 0; otherwise keep coder fallback (tie-break / coarse cue)."""
    if not ranked_pairs:
        return fallback_label
    top_label, top_s = ranked_pairs[0]
    if top_s <= 0.0:
        return fallback_label
    return top_label


def _sync_label_coder_and_adjudicator_from_scores(out: dict[str, Any]) -> None:
    """Set primary label + adjudicator final to highest-scoring code (when scores are informative)."""
    lc = out.get("label_coder")
    if not isinstance(lc, dict):
        return
    ranked = lc.get("label_scores_ranked") or []
    if not ranked:
        return
    labels = lc.get("labels") or []
    fallback = ""
    if labels and isinstance(labels[0], dict):
        fallback = str(labels[0].get("label") or "")
    pairs: list[tuple[str, float]] = []
    for r in ranked:
        if isinstance(r, dict) and "label" in r and "score" in r:
            try:
                pairs.append((str(r["label"]), float(r["score"])))
            except (TypeError, ValueError):
                continue
    if not pairs:
        return
    pairs.sort(key=lambda x: (-x[1], x[0]))
    final = _pick_final_label_from_ranked(pairs, fallback)
    if labels and isinstance(labels[0], dict):
        labels[0]["label"] = final
    adj = out.setdefault("adjudicator", {})
    finals = adj.get("final_labels")
    if not isinstance(finals, list) or not finals:
        finals = [{"span_ref": 0, "label": final, "decision": "accept_coder", "rationale": ""}]
        adj["final_labels"] = finals
    # Only adjust the first span’s final label (multi-span stays otherwise untouched).
    if finals and isinstance(finals[0], dict):
        f0 = finals[0]
        f0["label"] = final
        f0["decision"] = f0.get("decision") or "accept_coder"
        prev = (f0.get("rationale") or "").strip()
        extra = f"Final label = **{final}** (highest score among all `label_scores`)."
        f0["rationale"] = f"{extra} {prev}".strip() if prev else extra


def _ensure_label_coder_full_scores(
    out: dict[str, Any],
    cleaned_prompt: str,
    allowed_codes: list[str],
) -> None:
    lc = out.get("label_coder")
    if not isinstance(lc, dict):
        lc = {}
        out["label_coder"] = lc
    merged = _merge_label_scores_with_heuristic(lc, cleaned_prompt, allowed_codes)
    _enrich_label_coder_scores(lc, merged, allowed_codes=allowed_codes)


def _ensure_boundary_critic_scores_close_challenge(out: dict[str, Any]) -> None:
    """When top two scores are close, add a refinement challenge for the Boundary Critic."""
    lc = out.get("label_coder") or {}
    if not lc.get("scores_close"):
        return
    ranked = lc.get("label_scores_ranked") or []
    if len(ranked) < 2:
        return
    top, second = ranked[0], ranked[1]
    if not isinstance(top, dict) or not isinstance(second, dict):
        return
    bc = out.get("boundary_critic")
    if not isinstance(bc, dict):
        bc = {}
        out["boundary_critic"] = bc
    challenges = bc.get("challenges")
    if not isinstance(challenges, list):
        challenges = []
    if any(
        isinstance(c, dict) and "margin=" in (c.get("reason") or "")
        for c in challenges
    ):
        return
    assigned = ""
    labs = lc.get("labels") or []
    if labs and isinstance(labs[0], dict):
        assigned = str(labs[0].get("label") or "")
    challenges.append({
        "span_ref": 0,
        "assigned_label": assigned,
        "question": (
            "Top two label scores are close — refine tier1/tier2: which code fits the "
            "primary intent per golden-labels?"
        ),
        "reason": (
            f"Scores are close (margin={lc.get('label_scores_margin_top2')}). "
            f"Compare {top.get('label')} ({top.get('score')}) vs {second.get('label')} ({second.get('score')}). "
            "Adjust boundary if evidence supports the runner-up."
        ),
        "suggested_alternative": str(second.get("label") or ""),
    })
    bc["challenges"] = challenges


def _postprocess_pipeline_output(
    out: dict[str, Any],
    cleaned_prompt: str,
    allowed_codes: list[str],
) -> None:
    """Normalize scores, sync final label to argmax, add close-score challenges."""
    _ensure_label_coder_full_scores(out, cleaned_prompt, allowed_codes)
    _maybe_repair_concept_exploration_bias(out, cleaned_prompt, allowed_codes)
    _ensure_label_coder_full_scores(out, cleaned_prompt, allowed_codes)
    _sync_label_coder_and_adjudicator_from_scores(out)
    _ensure_boundary_critic_scores_close_challenge(out)


def _maybe_repair_concept_exploration_bias(
    out: dict[str, Any],
    cleaned_prompt: str,
    _allowed: list[str],
) -> None:
    """
    If the model labels everything Cognitive.concept_exploration but content scores favor
    another code, replace final/adjudicator labels with the heuristic best (when confident).
    """
    scores = _label_scores(cleaned_prompt)
    best_h, best_s = _best_label_from_scores(scores)
    concept_s = scores.get("Cognitive.concept_exploration", 0.0)
    if best_h == "Cognitive.concept_exploration" or best_s < 2.0:
        return
    if best_s <= concept_s + 0.5:
        return

    adj = out.get("adjudicator") or {}
    finals = adj.get("final_labels") or []
    if not isinstance(finals, list):
        return
    all_concept = all(
        isinstance(f, dict) and (f.get("label") == "Cognitive.concept_exploration")
        for f in finals
    )
    if not all_concept or not finals:
        return

    note = (
        f"Heuristic alignment: content scores favor {best_h} over Cognitive.concept_exploration "
        f"(see golden-labels tier1/tier2 rules)."
    )
    for f in finals:
        if not isinstance(f, dict):
            continue
        f["label"] = best_h
        f["decision"] = "combine_both"
        prev = (f.get("rationale") or "").strip()
        f["rationale"] = f"{note} Prior: {prev}" if prev else note

    lc = out.get("label_coder") or {}
    labels = lc.get("labels") or []
    if isinstance(labels, list):
        for row in labels:
            if isinstance(row, dict) and row.get("label") == "Cognitive.concept_exploration":
                row["label"] = best_h
                row["rationale"] = note
        lc["revision_note"] = note
    se = out.get("signal_extractor") or {}
    cands = se.get("candidate_signals") or []
    if isinstance(cands, list) and cands and isinstance(cands[0], dict):
        top3 = [c for c, s in sorted(scores.items(), key=lambda kv: -kv[1])[:3]]
        cands[0]["candidates"] = top3
        cands[0]["reason"] = "Ranked candidates from content-based scoring; avoids default concept_exploration."


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
    "label_scores": {{ "<each Tier1.tier2 from allowed list>": <number>, ... }},
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
- **Labeling procedure (mandatory):** For each span, decide **Tier1 first** (Cognitive vs Metacognitive vs Coordinative vs Socio-emotional), then **Tier2** within that tier. The label must match the **primary intent** of the quoted evidence, not a generic default.
- **Do NOT default to Cognitive.concept_exploration.** Use it only when the utterance is mainly about **concepts/definitions/learning meanings** (e.g. what a term means, clarifying a concept). Do **not** use it for: pure laughter/reactions, thanks/praise, task splitting/roles, planning how to solve, checking progress, judging output quality, or picking/correct answers—those map to other codes in the list above.
- **Evidence:** Every Label Coder and Adjudicator label must cite a **verbatim substring** of the prompt in `evidence_used` / rationale; candidate_signals should list 1–3 plausible codes when ambiguous.
- **Label Coder must output `label_scores`:** an object with **every** code in the allowed list as a key exactly once, each value a **non-negative number** (relative strength / confidence). Higher = better match. The pipeline will rank codes and set the **final label to the highest score** when the top score is informative; if scores are **close** (small margin between #1 and #2), the Boundary Critic should refine the boundary.
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
        out = chat_completions_json(cfg=cfg, messages=messages, temperature=0.1, max_tokens=2400)
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
    _postprocess_pipeline_output(out, cleaned_prompt, allowed)
    return out


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
    _load_taxonomy()

    # Prefer LLM-backed pipeline when configured; fallback to heuristics.
    if _llm_enabled():
        llm_out = _run_llm_pipeline(cleaned, context)
        if llm_out is not None:
            return llm_out

    chosen, cand_list, score_map = _infer_label_from_prompt(cleaned)
    top_score = score_map.get(chosen, 0.0)
    second_best = sorted(score_map.items(), key=lambda kv: -kv[1])[1][1] if len(score_map) > 1 else 0.0

    # --- Signal Extractor ---
    full_span = cleaned
    start = 0
    end = len(full_span)
    evidence_spans = [{"span": full_span, "start": start, "end": end, "reason": "Full prompt span for heuristic labeling."}]
    candidate_signals = [
        {
            "span_ref": 0,
            "candidates": cand_list,
            "reason": "Ranked by keyword/content scores; label must match primary intent (not a single default).",
        }
    ]
    ambiguity: list[dict[str, Any]] = []
    if top_score > 0 and second_best > 0 and (top_score - second_best) < 1.5:
        ambiguity.append({"span_ref": 0, "reason": "Top two label scores are close; tier1/tier2 boundary may be ambiguous."})
    elif top_score <= 0:
        ambiguity.append({"span_ref": 0, "reason": "Weak keyword match; label inferred from coarse cues only."})

    signal_extractor = {
        "evidence_spans": evidence_spans,
        "candidate_signals": candidate_signals,
        "ambiguity": ambiguity,
    }

    # --- Label Coder (draft) ---
    rationale = (
        f"Tier1/tier2 from content scoring: primary={chosen} "
        f"(score={top_score:.1f}). Not defaulted to concept_exploration unless cues match."
    )
    label_coder = {
        "labels": [
            {
                "span_ref": 0,
                "label": chosen,
                "evidence_used": full_span[:80] + ("..." if len(full_span) > 80 else ""),
                "rationale": rationale,
            }
        ],
        "uncertain": [] if not ambiguity else ["See signal_extractor.ambiguity for competing interpretations."],
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
        label_coder["revision_note"] = (
            "Keeping as monitoring: speaker is checking correctness of reasoning (process), "
            "not evaluating a final solution."
        )

    out: dict[str, Any] = {
        "prompt": cleaned,
        "context": context,
        "signal_extractor": signal_extractor,
        "label_coder": label_coder,
        "boundary_critic": boundary_critic,
        "adjudicator": {
            "final_labels": [],
            "uncertain": label_coder.get("uncertain", []),
            "retry": None,
        },
    }
    _postprocess_pipeline_output(out, cleaned, _taxonomy_codes())
    return out
