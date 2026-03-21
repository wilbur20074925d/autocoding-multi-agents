"""
Rule-based autocoding pipeline.

Runs: signal_extractor → label_coder → boundary_critic → (label_coder revision) → adjudicator.
Returns structured output for the Discord dispatcher. Uses keyword/heuristic rules
when no LLM runtime is available; replace with OpenClaw/LLM calls for production.
"""

from __future__ import annotations

import csv
import math
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


# --- Label scoring: semantic (LLM) + semantic proxy (no-LLM fallback) ---
# Each taxonomy code gets an independent score in [0, 5.00] with 2 decimal places.
# Heuristic path uses tier/tier2 cues + smoothing so prompts rarely collapse to all zeros.

SCORE_MAX = 5.0
SCORE_DECIMALS = 2
# On 0–5 scale: small margin between #1 and #2 ⇒ Boundary Critic refines.
SCORE_CLOSE_THRESHOLD = 0.75


def _clamp_round_score(x: float) -> float:
    return round(max(0.0, min(SCORE_MAX, float(x))), SCORE_DECIMALS)


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


def _baseline_semantic_spread(t: str, codes: list[str]) -> dict[str, float]:
    """
    When keyword affinity is flat, spread scores from discourse cues (length, pronouns, punctuation).
    Yields non-zero semantic-style relevance on 0–5 scale.
    """
    n = len(t)
    raw: dict[str, float] = {c: 0.35 for c in codes}
    raw["Cognitive.concept_exploration"] += 0.9 + min(1.2, n / 120.0)
    raw["Cognitive.solution_development"] += 0.85 + min(1.0, n / 150.0)
    raw["Metacognitive.planning"] += 0.95
    raw["Metacognitive.monitoring"] += 0.75
    raw["Metacognitive.evaluating"] += 0.7
    raw["Coordinative.coordinate_participants"] += 0.65
    raw["Coordinative.coordinate_procedures"] += 0.6
    raw["Socio-emotional.emotional_expression"] += 0.8 if n < 80 else 0.5
    raw["Socio-emotional.encouragement"] += 0.55
    raw["Socio-emotional.self_disclosure"] += 0.6
    if "?" in t:
        for k in (
            "Cognitive.concept_exploration",
            "Cognitive.solution_development",
            "Metacognitive.planning",
            "Metacognitive.evaluating",
        ):
            raw[k] += 0.5
    if re.search(r"\b(we|our|us|group|team|let's|should we|everyone)\b", t):
        raw["Metacognitive.planning"] += 0.45
        raw["Coordinative.coordinate_participants"] += 0.5
        raw["Coordinative.coordinate_procedures"] += 0.35
    if re.search(r"\b(i |i'|my |me |myself)\b", t):
        raw["Socio-emotional.self_disclosure"] += 0.55
        raw["Socio-emotional.emotional_expression"] += 0.35
    mx = max(raw.values())
    if mx <= 0:
        return {c: _clamp_round_score(1.0) for c in codes}
    order = sorted(codes)
    logits = [raw[c] + 0.28 for c in order]
    m = max(logits)
    exps = [math.exp(x - m) for x in logits]
    ssum = sum(exps)
    probs = [e / ssum for e in exps]
    max_p = max(probs) or 1e-9
    return {
        order[i]: _clamp_round_score(max(0.05, SCORE_MAX * probs[i] / max_p))
        for i in range(len(order))
    }


def _semantic_proxy_scores(text: str) -> dict[str, float]:
    """
    Semantic proxy (no LLM): combine phrase cues + tier-level discourse signals, map to 0–5 per label.
    Designed so scores are rarely all zero — uses scaling to SCORE_MAX for the strongest dimension.
    """
    codes = _taxonomy_codes()
    t = (text or "").strip().lower()
    if not t:
        return {c: _clamp_round_score(0.5) for c in codes}

    raw: dict[str, float] = {c: 0.0 for c in codes}
    for label, phrases in _LABEL_SCORE_PATTERNS:
        if label not in raw:
            continue
        for p in phrases:
            if p in t:
                raw[label] += 2.2 if len(p) > 4 else 1.4

    # Discourse-level semantic cues (not single-keyword only)
    if re.search(r"\b(how should|what steps|strategy|approach|plan|first we|let's start|procedure)\b", t):
        raw["Metacognitive.planning"] += 1.8
    if re.search(
        r"\b(on track|progress|next question|move on|pace|behind schedule|time left|are we)\b", t,
    ):
        raw["Metacognitive.monitoring"] += 1.8
    if re.search(
        r"\b(quality|good enough|make sense|evaluate|weak|strong enough|lack|detail|correct\?)\b", t,
    ):
        raw["Metacognitive.evaluating"] += 1.8
    if re.search(
        r"\b(define|meaning|concept|what is|what are|what does|clarify|theory|taxonomy|bloom)\b", t,
    ):
        raw["Cognitive.concept_exploration"] += 1.6
    if re.search(
        r"\b(option|answer|choose|pick|solution|final answer|which one|correct option)\b", t,
    ):
        raw["Cognitive.solution_development"] += 1.6
    if re.search(r"\b(split|divide|allocate|who should|your part|roles|who does)\b", t):
        raw["Coordinative.coordinate_participants"] += 1.5
    if re.search(
        r"\b(go first|take turns|workflow|bullet|paragraph|in chat|group chat|doc|screen)\b", t,
    ):
        raw["Coordinative.coordinate_procedures"] += 1.4
    if re.search(
        r"\b(thank|thanks|good job|great job|nice work|appreciate|grateful|keep going)\b", t,
    ):
        raw["Socio-emotional.encouragement"] += 1.7
    if re.search(
        r"\b(i've|i have|not familiar|first time|experience|personally|never done)\b", t,
    ):
        raw["Socio-emotional.self_disclosure"] += 1.6
    if re.search(r"\b(haha|lol|funny|hilarious|frustrated|nervous|worried|😂|哈哈)\b", t):
        raw["Socio-emotional.emotional_expression"] += 2.0

    if "?" in t:
        raw["Metacognitive.planning"] += 0.9
        raw["Cognitive.concept_exploration"] += 0.7
        raw["Cognitive.solution_development"] += 0.6

    mx = max(raw.values())
    if mx <= 0:
        return _baseline_semantic_spread(t, codes)
    # Soft distribution over labels (semantic proxy): avoids a long tail of exact 0.00 scores.
    order = sorted(codes)
    logits = [raw[c] + 0.28 for c in order]
    m = max(logits)
    exps = [math.exp(x - m) for x in logits]
    ssum = sum(exps)
    probs = [e / ssum for e in exps]
    max_p = max(probs) or 1e-9
    return {
        order[i]: _clamp_round_score(max(0.05, SCORE_MAX * probs[i] / max_p))
        for i in range(len(order))
    }


def _label_scores(text: str) -> dict[str, float]:
    """Alias for repair / legacy callers — semantic proxy on 0–5 scale."""
    return _semantic_proxy_scores(text)


def _best_label_from_scores(scores: dict[str, float]) -> tuple[str, float]:
    if not scores:
        return "Cognitive.solution_development", 0.0
    best = max(scores.items(), key=lambda kv: kv[1])
    return best[0], best[1]


def _infer_label_from_prompt(text: str) -> tuple[str, list[str], dict[str, float]]:
    """
    Returns (chosen_label, ranked_candidates_desc, scores).
    Picks label from semantic proxy scores (0–5) — does **not** default to concept_exploration only.
    """
    scores = _semantic_proxy_scores(text)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    chosen, top = ranked[0][0], ranked[0][1]
    stripped = (text or "").strip().lower()
    if len(stripped) <= 20 and any(x in stripped for x in ("haha", "lol", "哈哈", "😂")):
        return "Socio-emotional.emotional_expression", [
            "Socio-emotional.emotional_expression",
            "Socio-emotional.encouragement",
        ], scores
    if top <= 0.05:
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
    candidates = [c for c, s in ranked[:5] if s > 0.01][:3]
    if not candidates:
        candidates = [chosen]
    return chosen, candidates, scores


def _segment_prompt_for_extraction(text: str) -> list[tuple[str, int, int]]:
    """
    Split prompt into sentence/clause-like segments with stable offsets.
    Falls back to one full segment when splitting is not possible.
    """
    if not text:
        return []
    spans: list[tuple[str, int, int]] = []
    # Split by punctuation/newline while preserving offsets.
    parts = re.split(r"([.!?;,\n])", text)
    cursor = 0
    buf = ""
    buf_start = 0
    for part in parts:
        if part == "":
            continue
        next_cursor = cursor + len(part)
        if not buf:
            buf_start = cursor
        buf += part
        cursor = next_cursor
        if re.fullmatch(r"[.!?;,\n]", part):
            seg = buf.strip()
            if seg:
                start = text.find(seg, buf_start, cursor + 1)
                if start >= 0:
                    end = start + len(seg)
                    spans.append((seg, start, end))
            buf = ""
    if buf.strip():
        seg = buf.strip()
        start = text.find(seg, buf_start)
        if start >= 0:
            spans.append((seg, start, start + len(seg)))

    # Remove tiny duplicates and keep deterministic order.
    dedup: list[tuple[str, int, int]] = []
    seen: set[tuple[int, int]] = set()
    for seg, s, e in spans:
        if e - s < 2:
            continue
        if (s, e) in seen:
            continue
        seen.add((s, e))
        dedup.append((seg, s, e))
    if not dedup:
        return [(text, 0, len(text))]
    return dedup[:8]


def _sentiment_tag(segment: str) -> str:
    t = segment.lower()
    if any(k in t for k in ("haha", "lol", "hilarious", "funny", "frustrated", "worried", "nervous", "angry", "sad", "😂", "哈哈")):
        return "affective"
    if re.search(r"\b(thank|thanks|good job|great job|nice|appreciate)\b", t):
        return "supportive"
    if re.search(r"\b(not familiar|first time|never done|i've|i have|my experience)\b", t):
        return "self_disclosure"
    return "neutral_task"


def _build_signal_extractor_output(cleaned: str) -> dict[str, Any]:
    segments = _segment_prompt_for_extraction(cleaned)
    evidence_spans: list[dict[str, Any]] = []
    candidate_signals: list[dict[str, Any]] = []
    ambiguity: list[dict[str, Any]] = []

    for idx, (seg, start, end) in enumerate(segments):
        chosen, cand_list, score_map = _infer_label_from_prompt(seg)
        ranked = sorted(score_map.items(), key=lambda kv: (-kv[1], kv[0]))
        top = ranked[0][1] if ranked else 0.0
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        s_tag = _sentiment_tag(seg)
        evidence_spans.append(
            {
                "span": seg,
                "start": start,
                "end": end,
                "reason": (
                    f"Segmented evidence (sentiment={s_tag}); preserve local intent before final label arbitration."
                ),
            }
        )
        candidate_signals.append(
            {
                "span_ref": idx,
                "candidates": cand_list,
                "reason": (
                    f"Top candidate={chosen} from semantic-fit scoring on this segment "
                    f"(top={top:.2f}, second={second:.2f})."
                ),
            }
        )
        if top <= 0.05:
            ambiguity.append(
                {
                    "span_ref": idx,
                    "reason": "Weak semantic signal in this segment.",
                }
            )
        elif second > 0.01 and (top - second) < SCORE_CLOSE_THRESHOLD:
            ambiguity.append(
                {
                    "span_ref": idx,
                    "reason": (
                        f"Close top-two scores for this segment (margin={(top-second):.2f}); "
                        "Boundary Critic should refine."
                    ),
                }
            )

    return {
        "evidence_spans": evidence_spans,
        "candidate_signals": candidate_signals,
        "ambiguity": ambiguity,
    }


def _enrich_label_coder_scores(
    label_coder: dict[str, Any],
    score_map: dict[str, float],
    *,
    allowed_codes: list[str],
    close_threshold: float = SCORE_CLOSE_THRESHOLD,
) -> None:
    """Attach full label_scores, ranked list, closeness flag, and Discord-ready display."""
    from cloudbot.discord.format import build_label_scores_display

    full = {code: _clamp_round_score(float(score_map.get(code, 0.0))) for code in allowed_codes}
    ranked = sorted(full.items(), key=lambda kv: (-kv[1], kv[0]))
    label_coder["label_scores"] = full
    label_coder["label_scores_ranked"] = [
        {"label": k, "score": round(v, 2)} for k, v in ranked
    ]
    margin = (ranked[0][1] - ranked[1][1]) if len(ranked) > 1 else ranked[0][1]
    label_coder["scores_close"] = bool(ranked[0][1] > 0.01 and margin < close_threshold)
    label_coder["label_scores_margin_top2"] = round(margin, 3)
    label_coder["label_scores_display"] = build_label_scores_display(full)


def _merge_label_scores_with_heuristic(
    lc: dict[str, Any],
    cleaned_prompt: str,
    allowed_codes: list[str],
) -> dict[str, float]:
    """
    LLM scores = semantic relevance (must be 0.00–5.00). Clamp and merge with semantic proxy
    when the model returns missing, invalid, or near-all-zero scores.
    """
    heur = _semantic_proxy_scores(cleaned_prompt)
    raw = lc.get("label_scores")
    if not isinstance(raw, dict):
        return heur
    merged: dict[str, float] = {}
    for code in allowed_codes:
        v = raw.get(code)
        if v is None:
            merged[code] = heur[code]
        else:
            try:
                merged[code] = _clamp_round_score(float(v))
            except (TypeError, ValueError):
                merged[code] = heur[code]
    max_llm = max(merged.values()) if merged else 0.0
    if max_llm < 0.05:
        return heur
    # Weak / flat LLM output: blend toward semantic proxy (still 0–5)
    if max_llm < 1.25:
        return {
            c: _clamp_round_score(0.55 * heur[c] + 0.45 * merged[c])
            for c in allowed_codes
        }
    return merged


def _pick_final_label_from_ranked(
    ranked_pairs: list[tuple[str, float]],
    fallback_label: str,
) -> str:
    """Pick argmax when top score is meaningful; otherwise keep coder fallback."""
    if not ranked_pairs:
        return fallback_label
    top_label, top_s = ranked_pairs[0]
    if top_s <= 0.01:
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


def _ensure_boundary_critic_ambiguity_challenge(out: dict[str, Any]) -> None:
    """
    If Signal Extractor already marked a close-score ambiguity, Boundary Critic must challenge.
    This prevents silent 'Challenges: None' when uncertainty is explicitly detected upstream.
    """
    se = out.get("signal_extractor") or {}
    ambiguities = se.get("ambiguity") or []
    if not isinstance(ambiguities, list) or not ambiguities:
        return
    close_amb = None
    for a in ambiguities:
        if not isinstance(a, dict):
            continue
        reason = str(a.get("reason") or "").lower()
        if "close top-two" in reason or "scores are close" in reason or "top two" in reason:
            close_amb = a
            break
    if close_amb is None:
        return

    bc = out.get("boundary_critic")
    if not isinstance(bc, dict):
        bc = {}
        out["boundary_critic"] = bc
    challenges = bc.get("challenges")
    if not isinstance(challenges, list):
        challenges = []
    if any(isinstance(c, dict) and "margin=" in str(c.get("reason") or "") for c in challenges):
        return
    if any(isinstance(c, dict) and "ambiguous" in str(c.get("question") or "").lower() for c in challenges):
        return

    lc = out.get("label_coder") or {}
    assigned = ""
    labs = lc.get("labels") or []
    if labs and isinstance(labs[0], dict):
        assigned = str(labs[0].get("label") or "")
    ranked = lc.get("label_scores_ranked") or []
    runner_up = ""
    if isinstance(ranked, list) and len(ranked) > 1 and isinstance(ranked[1], dict):
        runner_up = str(ranked[1].get("label") or "")

    challenges.append(
        {
            "span_ref": int(close_amb.get("span_ref", 0)),
            "assigned_label": assigned,
            "question": "Ambiguous close scores detected — should this span be re-labeled?",
            "reason": (
                "Signal Extractor marked close top-two ambiguity; Boundary Critic should refine "
                "tier1/tier2 boundary using golden-labels rules."
            ),
            "suggested_alternative": runner_up,
        }
    )
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
    _ensure_boundary_critic_ambiguity_challenge(out)


def _maybe_repair_concept_exploration_bias(
    out: dict[str, Any],
    cleaned_prompt: str,
    _allowed: list[str],
) -> None:
    """
    If the model labels everything Cognitive.concept_exploration but content scores favor
    another code, replace final/adjudicator labels with the heuristic best (when confident).
    """
    scores = _semantic_proxy_scores(cleaned_prompt)
    best_h, best_s = _best_label_from_scores(scores)
    concept_s = scores.get("Cognitive.concept_exploration", 0.0)
    if best_h == "Cognitive.concept_exploration" or best_s < 2.25:
        return
    if best_s <= concept_s + 0.35:
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
- **Signal Extractor precision:** split the prompt into multiple minimal evidence spans (sentence/clause level), attach precise start/end offsets, keep each span verbatim, and include sentiment-aware rationale per span (e.g. affective/supportive/neutral_task). Do not collapse to one full-span evidence unless the prompt is too short to segment.
- **Label Coder must output `label_scores`:** an object with **every** code in the allowed list as a key exactly once. Each value must be a number from **0.00 to 5.00** (two decimals), representing **semantic fit** of the utterance to that label (intent, not keyword counts). **5.00** = strongest match for that category; **0.00** = no meaningful fit. Judge each label **independently** by meaning (cognitive vs metacognitive vs coordinative vs socio-emotional, then tier2). The pipeline ranks codes and sets the **final label to the highest score** when informative; if #1 and #2 are **close** (small margin on this 0–5 scale), the Boundary Critic refines the boundary.
- Use golden-labels boundaries and decision rules below.
- Keep evidence spans minimal but sufficient; use span_ref=0 for the whole prompt if needed.
- Boundary Critic must only challenge, not decide.
- If Signal Extractor ambiguity says close top-two scores (or equivalent), Boundary Critic must output at least one challenge for that span (no empty challenges in this case).
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
    signal_extractor = _build_signal_extractor_output(cleaned)
    # Keep prior whole-prompt ambiguity signal so downstream behavior remains compatible.
    if (
        top_score > 0.01
        and second_best > 0.01
        and (top_score - second_best) < SCORE_CLOSE_THRESHOLD
    ):
        signal_extractor["ambiguity"].append(
            {"span_ref": 0, "reason": "Whole-prompt top two semantic-fit scores are close; tier1/tier2 boundary may be ambiguous."}
        )
    elif top_score <= 0.05:
        signal_extractor["ambiguity"].append(
            {"span_ref": 0, "reason": "Whole-prompt semantic signal is weak; use segment-level evidence carefully."}
        )

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
                "evidence_used": cleaned[:80] + ("..." if len(cleaned) > 80 else ""),
                "rationale": rationale,
            }
        ],
        "uncertain": [] if not signal_extractor.get("ambiguity") else ["See signal_extractor.ambiguity for competing interpretations."],
        "revision_note": None,
    }

    # --- Boundary Critic ---
    challenges: list[dict[str, Any]] = []
    # Enforce challenge when extractor already marks close-score ambiguity.
    for a in signal_extractor.get("ambiguity", []):
        if not isinstance(a, dict):
            continue
        reason = str(a.get("reason") or "").lower()
        if "close top-two" in reason or "top two" in reason:
            challenges.append(
                {
                    "span_ref": int(a.get("span_ref", 0)),
                    "assigned_label": chosen,
                    "question": "Top two scores are close — should this label be revised?",
                    "reason": "Extractor ambiguity indicates close scores; refine boundary by golden-labels criteria.",
                    "suggested_alternative": cand_list[1] if len(cand_list) > 1 else "",
                }
            )
            break
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
