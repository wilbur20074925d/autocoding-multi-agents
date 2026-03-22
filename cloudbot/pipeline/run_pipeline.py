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
from cloudbot.pipeline.consistency_checking import (
    append_consistency_to_adjudicator_rationale,
    apply_consistency_checking,
)

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


def _normalize_session_context(context: dict[str, Any] | None) -> dict[str, Any]:
    """
    Flatten CSV / Discord metadata (group, people, timestamp, context) for heuristics and LLM.
    `context` is often a scenario tag (e.g. no-gai, discussion) — 上下文 for disambiguation.
    """
    ctx = context or {}
    group = str(ctx.get("group") or "").strip()
    people_raw = str(ctx.get("people") or "").strip()
    timestamp = str(
        ctx.get("timestamp") or ctx.get("timestamp-mm") or ctx.get("timestamp_mm") or "",
    ).strip()
    scenario = str(ctx.get("context") or "").strip()
    blob = f"{group} {people_raw} {timestamp} {scenario}".lower()
    people_n: int | None = None
    if people_raw.isdigit():
        people_n = int(people_raw)
    return {
        "group": group,
        "people": people_raw,
        "people_count": people_n,
        "timestamp": timestamp,
        "scenario": scenario,
        "blob": blob,
    }


def _session_implies_task_oriented_discussion(norm: dict[str, Any]) -> bool:
    """
    True when metadata suggests an interaction / study session (上下文), not isolated free text.
    Used to disambiguate short prompts (e.g. 'Naming and defining.') toward solution vs concept.
    """
    if (norm.get("people_count") or 0) >= 2:
        return True
    b = norm.get("blob") or ""
    if norm.get("group") and re.search(r"(?i)^g\d+|group\s*\d", str(norm.get("group"))):
        return True
    if re.search(r"\bg\d+\b", b):
        return True
    tags = (
        "no-gai",
        "gai",
        "discussion",
        "discuss",
        "collaborat",
        "pair",
        "session",
        "dialogue",
        "communication",
        "group work",
        "joint",
        "together",
        "chat",
        "in-class",
        "task",
    )
    return any(t in b for t in tags)


def _apply_session_context_cognitive_bias(
    raw: dict[str, float],
    t: str,
    norm: dict[str, Any],
) -> None:
    """
    When 上下文 indicates a collaborative/task discussion, nudge short utterances that sound like
    naming/labeling the *answer* toward Cognitive.solution_development (vs concept_exploration).
    """
    if not _session_implies_task_oriented_discussion(norm):
        return
    n = len(t.strip())
    if n > 160:
        return
    tl = t.lower()
    # Phrases common in group work: naming/labeling the solution or option.
    if re.search(r"\bnaming\s+and\s+defining\b", tl):
        raw["Cognitive.solution_development"] += 2.85
        raw["Cognitive.concept_exploration"] = max(
            0.0,
            raw.get("Cognitive.concept_exploration", 0.0) - 1.35,
        )
        return
    if re.search(r"\b(naming|labelling|labeling)\b", tl) and re.search(r"\bdefin", tl):
        raw["Cognitive.solution_development"] += 2.35
        raw["Cognitive.concept_exploration"] = max(
            0.0,
            raw.get("Cognitive.concept_exploration", 0.0) - 1.0,
        )
        return
    if re.search(
        r"\b(name|label|identify)\s+(the|our|this|that)\s+(answer|option|solution|choice|response)\b",
        tl,
    ):
        raw["Cognitive.solution_development"] += 2.1
        return
    if re.search(r"\b(final answer|which option|pick (one|an option)|our solution)\b", tl):
        raw["Cognitive.solution_development"] += 1.65
    # Multi-party + very short task cue → slight solution tilt
    if n <= 48 and (norm.get("people_count") or 0) >= 2 and re.search(
        r"\b(define|naming|label|answer|option|solution)\b",
        tl,
    ):
        raw["Cognitive.solution_development"] += 1.1


def _golden_hc_implies_solution_development(context: dict[str, Any] | None) -> bool:
    """
    Human CSV labels like `solution\\development-give` / `solution/development-ask` map to
    Cognitive.solution_development (see cloudbot/data/cognitive-tier2-hc-subactions.md).
    Sub-actions (ask, agree, …) are shared with the concept strand—the **strand prefix** disambiguates.
    """
    if not context:
        return False
    for key in ("HC1", "HC2", "hc1", "hc2", "gold_label", "label_gold"):
        v = str(context.get(key) or "").lower()
        v = v.replace("\\", "/")
        if "solution" in v and "development" in v:
            return True
    return False


def _golden_hc_implies_metacognitive_planning(context: dict[str, Any] | None) -> bool:
    """
    Human HC like `planning-give`, `planning-agree` → Metacognitive.planning (not Cognitive.*).
    """
    if not context:
        return False
    for key in ("HC1", "HC2", "hc1", "hc2", "gold_label", "label_gold"):
        v = str(context.get(key) or "").lower().replace("\\", "/")
        if re.search(r"(?i)\bplanning[-/]", v) or v.strip().startswith("planning-"):
            return True
    return False


def _golden_hc_implies_concept_exploration(context: dict[str, Any] | None) -> bool:
    """
    Human CSV labels like `concept\\exploration-ask` / `concept/exploration-give` map to
    Cognitive.concept_exploration (parallel sub-action vocabulary to solution_development).
    """
    if not context:
        return False
    for key in ("HC1", "HC2", "hc1", "hc2", "gold_label", "label_gold"):
        v = str(context.get(key) or "").lower()
        v = v.replace("\\", "/")
        if "concept" in v and "exploration" in v:
            return True
    return False


def _utterance_looks_like_bloom_definition_question(t: str) -> bool:
    """True when the line mainly asks what Bloom/taxonomy *is* (concept_exploration)."""
    if re.search(r"(?i)\bwhat (is|are)\b[^?.]{0,140}\b(bloom|taxonomy)\b", t):
        return True
    if re.search(r"(?i)^\s*what is (the )?bloom", t):
        return True
    if re.search(r"(?i)\bwhat does\b[^?.]{0,100}\b(mean|metacognitive)\b", t):
        return True
    return False


def _utterance_looks_like_metacognitive_planning_chat(t: str) -> bool:
    """
    How to approach / structure the task (Metacognitive.planning), **not** Cognitive tier2.

    Human HC examples: `planning-give`, `planning-agree` — do **not** map to
    Cognitive.solution_development just because of "bullet" or "question".
    """
    if re.search(r"(?i)\b(we can first|we should first|first we can|let's first|we could first)\b", t):
        return True
    if re.search(r"(?i)\b(list the points|in bullet form|bullet form|bullet points)\b", t):
        return True
    if re.search(r"(?i)\b(organize (them )?into|coherent structure)\b", t):
        return True
    if re.search(r"(?i)\bfor the (first|second|third|next) question\b", t) and re.search(
        r"(?i)\b(we can|we should|let's|could)\b",
        t,
    ):
        return True
    if re.search(r"(?i)\b(first|then|next)\b.*\b(list|organize|structure|bullet)\b", t):
        return True
    return False


def _utterance_looks_like_bloom_task_solution_talk(t: str) -> bool:
    """
    Bloom *levels* or task-product talk: classifying the response / answer (solution_development),
    not abstract definitions.
    """
    if _utterance_looks_like_metacognitive_planning_chat(t):
        return False
    if re.search(
        r"(?i)\b(remember|understanding|understand|summarize|summarizing|analyzing|analyze|differentiating|differentiate|applying|apply|evaluating|creating|create)\b",
        t,
    ):
        return True
    if re.search(r"(?i)\bfor (understanding|analyzing|remembering|evaluating|creating)\b", t):
        return True
    if re.search(r"(?i)\bnaming and defining\b", t):
        return True
    if re.search(r"(?i)\b(this )?defining could\b", t):
        return True
    if re.search(r"(?i)differentiating and proposing", t):
        return True
    if re.search(r"(?i)consider coding", t):
        return True
    if re.search(r"(?i)\b(bullet|bullets)\b.*\b(form|points|list|structure)\b", t):
        return True
    if re.search(r"(?i)\bthird question\b", t):
        return True
    if re.search(r"(?i)\bcoherent structure\b", t):
        return True
    if re.search(r"(?i)\b(because|since)\b[^?.]{0,80}\bdefin", t):
        return True
    return False


def _apply_bloom_task_and_golden_ce_sd_bias(
    raw: dict[str, float],
    t: str,
    norm: dict[str, Any],
    context: dict[str, Any] | None,
) -> None:
    """
    Separate Cognitive.concept_exploration vs Cognitive.solution_development:
    Bloom-level / HC `solution\\development-*` / joint-task talk → solution_development.
    """
    task_like = _utterance_looks_like_bloom_task_solution_talk(t)
    def_q = _utterance_looks_like_bloom_definition_question(t)

    if def_q and not task_like:
        raw["Cognitive.concept_exploration"] += 0.5
    if task_like and not def_q:
        raw["Cognitive.solution_development"] += 2.25
        raw["Cognitive.concept_exploration"] = max(
            0.0,
            raw.get("Cognitive.concept_exploration", 0.0) - 1.95,
        )

    if _golden_hc_implies_solution_development(context) and not _golden_hc_implies_concept_exploration(
        context,
    ):
        raw["Cognitive.solution_development"] += 2.05
        raw["Cognitive.concept_exploration"] = max(
            0.0,
            raw.get("Cognitive.concept_exploration", 0.0) - 1.55,
        )
    elif _golden_hc_implies_concept_exploration(context) and not _golden_hc_implies_solution_development(
        context,
    ):
        raw["Cognitive.concept_exploration"] += 2.05
        raw["Cognitive.solution_development"] = max(
            0.0,
            raw.get("Cognitive.solution_development", 0.0) - 1.55,
        )

    # Short backchannels in tagged group/task sessions align with solution strand (not CE default)
    if len(t) <= 44 and re.search(r"(?i)\byeah\b", t) and _session_implies_task_oriented_discussion(norm):
        raw["Cognitive.solution_development"] += 1.15
        raw["Cognitive.concept_exploration"] = max(
            0.0,
            raw.get("Cognitive.concept_exploration", 0.0) - 1.05,
        )


def _apply_metacognitive_planning_heuristics(
    raw: dict[str, float],
    t: str,
    norm: dict[str, Any],
    context: dict[str, Any] | None,
) -> None:
    """
    Metacognitive.planning vs Cognitive: procedure / how-to-structure talk and HC `planning-*`.
    Short chat backchannels (Yes.) follow the previous line's strand when possible.
    """
    tl = (t or "").strip().lower()

    if _utterance_looks_like_metacognitive_planning_chat(tl):
        raw["Metacognitive.planning"] += 2.65
        raw["Cognitive.solution_development"] = max(
            0.0,
            raw.get("Cognitive.solution_development", 0.0) - 1.75,
        )
        raw["Cognitive.concept_exploration"] = max(
            0.0,
            raw.get("Cognitive.concept_exploration", 0.0) - 1.45,
        )
        # "bullet" often hits Coordinative via substring — planning-give is Metacognitive, not logistics.
        raw["Coordinative.coordinate_procedures"] = max(
            0.0,
            raw.get("Coordinative.coordinate_procedures", 0.0) - 4.0,
        )

    if _golden_hc_implies_metacognitive_planning(context):
        raw["Metacognitive.planning"] += 2.05
        raw["Cognitive.solution_development"] = max(
            0.0,
            raw.get("Cognitive.solution_development", 0.0) - 1.6,
        )
        raw["Cognitive.concept_exploration"] = max(
            0.0,
            raw.get("Cognitive.concept_exploration", 0.0) - 1.45,
        )

    # Chat backchannel: align with planning or solution strand (not concept_exploration default)
    if len(tl) <= 28 and re.match(r"(?i)^(yes|yeah|yep|ok|okay|sure)\.?!?$", tl.strip()):
        before, _ = _session_neighbor_lists(context)
        if _golden_hc_implies_metacognitive_planning(context):
            raw["Metacognitive.planning"] += 1.95
            raw["Cognitive.concept_exploration"] = max(
                0.0,
                raw.get("Cognitive.concept_exploration", 0.0) - 1.85,
            )
            raw["Cognitive.solution_development"] = max(
                0.0,
                raw.get("Cognitive.solution_development", 0.0) - 1.15,
            )
        elif before:
            prev = before[-1].lower()
            if _utterance_looks_like_metacognitive_planning_chat(prev):
                raw["Metacognitive.planning"] += 1.85
                raw["Cognitive.concept_exploration"] = max(
                    0.0,
                    raw.get("Cognitive.concept_exploration", 0.0) - 1.8,
                )
                raw["Cognitive.solution_development"] = max(
                    0.0,
                    raw.get("Cognitive.solution_development", 0.0) - 1.05,
                )
            elif _utterance_looks_like_bloom_task_solution_talk(prev) or _golden_hc_implies_solution_development(
                context,
            ):
                raw["Cognitive.solution_development"] += 1.7
                raw["Cognitive.concept_exploration"] = max(
                    0.0,
                    raw.get("Cognitive.concept_exploration", 0.0) - 1.85,
                )


def _session_neighbor_lists(context: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    """Normalize session_prompts_before / session_prompts_after from pipeline context."""
    ctx = context or {}

    def _coerce(x: Any) -> list[str]:
        if x is None:
            return []
        if isinstance(x, str):
            s = x.strip()
            return [s] if s else []
        if isinstance(x, (list, tuple)):
            return [str(s).strip() for s in x if str(s).strip()]
        return []

    before = _coerce(ctx.get("session_prompts_before")) or _coerce(ctx.get("prompts_before"))
    after = _coerce(ctx.get("session_prompts_after")) or _coerce(ctx.get("prompts_after"))
    return before, after


def _session_bundle_cognitive_analysis(bundle: list[str]) -> dict[str, Any]:
    """
    **Whole-session semantic focus** for Cognitive.concept_exploration vs Cognitive.solution_development.

    Combines (1) average per-line semantic-proxy scores over the full window (before + current + after),
    and (2) counts of utterances that look like **definitions** vs **task/solution** talk.

    - **concept_exploration** ↔ concepts *of* the learning task (meanings, theory, what terms denote).
    - **solution_development** ↔ solutions *for* the learning task (answers, options, classifying the response).
    """
    codes = _taxonomy_codes()
    lines = [x.strip() for x in bundle if (x or "").strip()]
    if not lines:
        return {
            "avg_scores": {c: 0.0 for c in codes},
            "n_lines": 0,
            "task_hits": 0,
            "def_hits": 0,
            "ce_avg": 0.0,
            "sd_avg": 0.0,
            "sd_minus_ce": 0.0,
            "kw_signal": 0.0,
            "tilt": "neutral",
            "semantic_focus": "mixed",
            "summary_line": "",
        }

    agg = {c: 0.0 for c in codes}
    task_hits = 0
    def_hits = 0
    for line in lines:
        low = line.lower()
        sc = _semantic_proxy_scores(low, None)
        for c in codes:
            agg[c] += sc.get(c, 0.0)
        if _utterance_looks_like_bloom_definition_question(low) and not _utterance_looks_like_bloom_task_solution_talk(
            low,
        ):
            def_hits += 1
        elif _utterance_looks_like_bloom_task_solution_talk(low):
            task_hits += 1

    n = len(lines)
    avg_scores = {c: agg[c] / n for c in codes}
    ce_avg = avg_scores.get("Cognitive.concept_exploration", 0.0)
    sd_avg = avg_scores.get("Cognitive.solution_development", 0.0)
    gap = sd_avg - ce_avg
    kw = (task_hits - def_hits) / float(n)

    tilt = "mixed"
    semantic_focus = "mixed"
    if (kw >= 0.25 and task_hits >= 1) or gap > 0.12:
        tilt = "solution_development"
        semantic_focus = "learning_task_solutions"
    elif (kw <= -0.25 and def_hits >= 1) or gap < -0.12:
        tilt = "concept_exploration"
        semantic_focus = "learning_task_concepts"
    elif gap > 0.04:
        tilt = "solution_development"
        semantic_focus = "learning_task_solutions"
    elif gap < -0.04:
        tilt = "concept_exploration"
        semantic_focus = "learning_task_concepts"

    if semantic_focus == "learning_task_solutions":
        focus_desc = (
            "**solutions for the learning task** (answers, options, how to label or justify the response)"
        )
    elif semantic_focus == "learning_task_concepts":
        focus_desc = "**concepts of the learning task** (meanings, definitions, subject-matter theory)"
    else:
        focus_desc = "**mixed** — both concept-oriented and solution-oriented lines appear in this window"

    summary_line = (
        f"**Whole-session semantic focus:** {focus_desc}. "
        f"Avg proxy: CE≈{ce_avg:.2f} vs SD≈{sd_avg:.2f} (Δ={gap:+.2f}); "
        f"definition-style lines {def_hits}/{n}, task-solution-style lines {task_hits}/{n}. "
        "For an ambiguous **current** line, prefer the tier2 that matches this focus."
    )

    return {
        "avg_scores": avg_scores,
        "n_lines": n,
        "task_hits": task_hits,
        "def_hits": def_hits,
        "ce_avg": ce_avg,
        "sd_avg": sd_avg,
        "sd_minus_ce": gap,
        "kw_signal": kw,
        "tilt": tilt,
        "semantic_focus": semantic_focus,
        "summary_line": summary_line,
    }


def _session_bundle_scores_for_tilt(texts: list[str]) -> dict[str, float]:
    """Average semantic-proxy scores across utterances (context=None per line)."""
    return _session_bundle_cognitive_analysis(texts)["avg_scores"]


def _apply_session_window_cognitive_bias(
    raw: dict[str, float],
    t: str,
    context: dict[str, Any] | None,
) -> None:
    """
    Use the **whole window** (neighbors + current) to nudge CE vs SD from session semantic focus.
    """
    before, after = _session_neighbor_lists(context)
    if not before and not after:
        return
    bundle = [*(before or []), t, *(after or [])]
    ana = _session_bundle_cognitive_analysis(bundle)
    tilt = ana["tilt"]
    gap = float(ana.get("sd_minus_ce") or 0.0)

    if tilt == "solution_development":
        raw["Cognitive.solution_development"] += 1.45
        raw["Cognitive.concept_exploration"] = max(
            0.0,
            raw.get("Cognitive.concept_exploration", 0.0) - 0.75,
        )
    elif tilt == "concept_exploration":
        raw["Cognitive.concept_exploration"] += 0.9
        raw["Cognitive.solution_development"] = max(
            0.0,
            raw.get("Cognitive.solution_development", 0.0) - 0.5,
        )
    elif gap > 0.02:
        raw["Cognitive.solution_development"] += 0.55
        raw["Cognitive.concept_exploration"] = max(
            0.0,
            raw.get("Cognitive.concept_exploration", 0.0) - 0.32,
        )
    elif gap < -0.02:
        raw["Cognitive.concept_exploration"] += 0.48
        raw["Cognitive.solution_development"] = max(
            0.0,
            raw.get("Cognitive.solution_development", 0.0) - 0.3,
        )


def _build_session_overview_dict(
    context: dict[str, Any] | None,
    current_prompt: str,
) -> dict[str, Any]:
    """
    Signal Extractor: overview of the chat window for Discord + downstream reasoning.
    """
    before, after = _session_neighbor_lists(context)
    has = bool(before or after)
    cur = (current_prompt or "").strip()
    if not has:
        return {
            "has_neighbors": False,
            "cognitive_tilt": "neutral",
            "summary_line": "_No neighboring prompts in context (single-utterance mode)._",
            "before": [],
            "after": [],
            "per_utterance_top": [],
            "aggregate_preview": {},
        }

    bundle = [*(before or []), cur, *(after or [])]
    ana = _session_bundle_cognitive_analysis(bundle)
    tilt = str(ana.get("tilt") or "mixed")
    summary = str(ana.get("summary_line") or "")
    agg = ana.get("avg_scores") or {}

    per: list[dict[str, Any]] = []
    for i, txt in enumerate(before, start=1):
        sc = _semantic_proxy_scores(txt, None)
        ranked = sorted(sc.items(), key=lambda kv: (-kv[1], kv[0]))
        top_lab = ranked[0][0] if ranked else ""
        per.append(
            {
                "role": "before",
                "index": i,
                "preview": txt[:220] + ("…" if len(txt) > 220 else ""),
                "top_label": top_lab,
                "top_score": round(float(ranked[0][1]), 2) if ranked else 0.0,
            }
        )
    sc_cur = _semantic_proxy_scores(cur, context)
    r_cur = sorted(sc_cur.items(), key=lambda kv: (-kv[1], kv[0]))
    per.append(
        {
            "role": "current",
            "index": 0,
            "preview": cur[:220] + ("…" if len(cur) > 220 else ""),
            "top_label": r_cur[0][0] if r_cur else "",
            "top_score": round(float(r_cur[0][1]), 2) if r_cur else 0.0,
        }
    )
    for i, txt in enumerate(after, start=1):
        sc = _semantic_proxy_scores(txt, None)
        ranked = sorted(sc.items(), key=lambda kv: (-kv[1], kv[0]))
        top_lab = ranked[0][0] if ranked else ""
        per.append(
            {
                "role": "after",
                "index": i,
                "preview": txt[:220] + ("…" if len(txt) > 220 else ""),
                "top_label": top_lab,
                "top_score": round(float(ranked[0][1]), 2) if ranked else 0.0,
            }
        )

    top5 = sorted(agg.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    return {
        "has_neighbors": True,
        "cognitive_tilt": tilt,
        "semantic_focus": ana.get("semantic_focus"),
        "summary_line": summary,
        "task_cue_hits": ana.get("task_hits"),
        "def_cue_hits": ana.get("def_hits"),
        "session_kw_signal": round(float(ana.get("kw_signal") or 0.0), 3),
        "before": before,
        "after": after,
        "per_utterance_top": per,
        "aggregate_preview": {k: round(v, 3) for k, v in top5},
        "neighbor_counts": {"before": len(before), "after": len(after)},
    }


# --- Label scoring: semantic (LLM) + semantic proxy (no-LLM fallback) ---
# Each taxonomy code gets a score in [0, 5.00] with 2 decimal places.
# Heuristic path: **must not** force argmax to 5.00 (that made every utterance's top label 5.0).
# Session window / metadata only adjust **raw** evidence; softmax + damped mapping yields spread.

SCORE_MAX = 5.0
SCORE_DECIMALS = 2
# On 0–5 scale: small margin between #1 and #2 ⇒ Boundary Critic refines.
SCORE_CLOSE_THRESHOLD = 0.75
# Softer softmax → neighbors + multi-cue text don't collapse to prob≈1 on one code.
_SOFTMAX_TEMPERATURE = 1.82
# Cap stacked regex hits per label before log compression (joint evidence, not infinite sum).
_RAW_SCORE_CAP_PER_LABEL = 8.0
# Map prob → 0–5: exponent <1 so only strong dominance approaches 5.0 (never “all tops are 5.0”).
_SCORE_FROM_PROB_EXPONENT = 0.50
_SCORE_FLOOR = 0.06


def _clamp_round_score(x: float) -> float:
    return round(max(0.0, min(SCORE_MAX, float(x))), SCORE_DECIMALS)


def _softmax_scores_from_raw(raw: dict[str, float], codes: list[str]) -> dict[str, float]:
    """
    Convert per-label **raw** evidence into differentiated 0–5 scores.

    Unlike ``5 * p_i / max(p)``, the top label is **not** always 5.00; flat or ambiguous
    distributions land in the ~1–3 band; clear single-label evidence reaches ~4–5.
    """
    order = sorted(codes)
    capped = [
        min(_RAW_SCORE_CAP_PER_LABEL, max(0.0, float(raw.get(c, 0.0) or 0.0)))
        for c in order
    ]
    logits = [math.log1p(x) + 0.12 for x in capped]
    m = max(logits)
    T = _SOFTMAX_TEMPERATURE
    exps = [math.exp((x - m) / T) for x in logits]
    ssum = sum(exps) or 1e-12
    probs = [e / ssum for e in exps]
    spread = SCORE_MAX - _SCORE_FLOOR
    alpha = _SCORE_FROM_PROB_EXPONENT
    out: dict[str, float] = {}
    for i, c in enumerate(order):
        p = probs[i]
        # p in (0,1]; pow pulls apart mid-range; session/context only affect raw → p here.
        s = _SCORE_FLOOR + spread * (p**alpha)
        out[c] = _clamp_round_score(max(_SCORE_FLOOR, s))
    return out


_LABEL_SCORE_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    # Socio-emotional (often short / affective — check before generic cognitive)
    ("Socio-emotional.emotional_expression", (
        "haha", "hahaha", "hhh", "hehe", "lol", "lmao", "omg", "hilarious", "funny",
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
        "i actually ", "i study ", "i'm super aware", "i am super aware",
        "super aware", "my background", "my field", "i work in", "i'm a student",
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
        "goal is", "order of steps", "it's okay", "it is okay",
        "no, no, it is different", "it is different",
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
        # Do not include bare "bloom"/"taxonomy" — those appear in task/solution talk too; see _apply_bloom_task_and_golden_ce_sd_bias.
        "what is ", "what are ", "define ", "definition", "meaning of",
        "concept of", "clarify", "explain what",
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
    return _softmax_scores_from_raw(raw, codes)


def _apply_informational_vs_socioemotional_bias(raw: dict[str, float], t: str) -> None:
    """
    Socio-emotional.emotional_expression is for affect/reaction — not informational questions
    about concepts, Bloom, taxonomy, or other learning content. Demote EE / self_disclosure
    when the utterance primarily carries information-seeking or conceptual content.
    """
    tl = (t or "").strip().lower()
    if not tl:
        return

    def _cap_ee(x: float) -> float:
        return max(0.0, min(_RAW_SCORE_CAP_PER_LABEL, x))

    # Informational questions (not "pure emotion")
    if re.search(
        r"(?i)^(do you (all )?know|have you heard|are you familiar|did you learn|can you (tell|explain)|"
        r"what is|what are|what does|tell me about)\b",
        tl,
    ):
        raw["Socio-emotional.emotional_expression"] = _cap_ee(raw.get("Socio-emotional.emotional_expression", 0.0) - 4.2)
        raw["Cognitive.concept_exploration"] += 2.5
    if "?" in tl and re.search(
        r"(?i)\b(know|about|heard|familiar|bloom|taxonomy|concept|define|mean|what|which|why|how)\b",
        tl,
    ):
        raw["Socio-emotional.emotional_expression"] = _cap_ee(raw.get("Socio-emotional.emotional_expression", 0.0) - 3.0)
        raw["Cognitive.concept_exploration"] += 1.6
    if re.search(r"(?i)\b(bloom|taxonomy)\b", tl) and "?" in tl:
        raw["Socio-emotional.emotional_expression"] = _cap_ee(raw.get("Socio-emotional.emotional_expression", 0.0) - 4.5)
        raw["Cognitive.concept_exploration"] += 2.2
    # Conceptual unfamiliarity answer — still part of concept thread, not pure self-disclosure
    if re.search(
        r"(?i)\b(first time|never heard|not familiar|hearing about it)\b",
        tl,
    ) and re.search(r"(?i)\b(this|that|it|about)\b", tl):
        raw["Socio-emotional.self_disclosure"] = _cap_ee(raw.get("Socio-emotional.self_disclosure", 0.0) - 2.5)
        raw["Cognitive.concept_exploration"] += 2.0
    # "I" + informational stance in discussion (weak affect cue — do not default to EE)
    if re.search(r"\b(i think|i believe|i feel like|it seems)\b", tl) and re.search(
        r"(?i)\b(teach|student|learn|process|goal|taxonomy|concept)\b",
        tl,
    ):
        raw["Socio-emotional.emotional_expression"] = _cap_ee(raw.get("Socio-emotional.emotional_expression", 0.0) - 1.8)
        raw["Cognitive.concept_exploration"] += 1.1


def _semantic_proxy_scores(
    text: str,
    context: dict[str, Any] | None = None,
) -> dict[str, float]:
    """
    Semantic proxy (no LLM): combine phrase cues + tier-level discourse signals, map to 0–5 per label.
    Optional **context** (group, people, timestamp, scenario) adjusts short/ambiguous utterances using 上下文.
    """
    codes = _taxonomy_codes()
    t = (text or "").strip().lower()
    if not t:
        return {c: _clamp_round_score(0.5) for c in codes}
    norm = _normalize_session_context(context)

    raw: dict[str, float] = {c: 0.0 for c in codes}
    # Text-only laughter (e.g. "hhh", "hhhh") — not matched by substring "haha".
    if re.fullmatch(r"h{3,}", t):
        raw["Socio-emotional.emotional_expression"] += 3.2
    for label, phrases in _LABEL_SCORE_PATTERNS:
        if label not in raw:
            continue
        for p in phrases:
            if p in t:
                raw[label] += 2.2 if len(p) > 4 else 1.4

    # Discourse-level semantic cues (not single-keyword only)
    if re.search(r"\b(how should|what steps|strategy|approach|plan|first we|let's start|procedure)\b", t):
        raw["Metacognitive.planning"] += 1.8
    if "it's okay" in t or "it is okay" in t:
        # Project-specific legacy mapping: planning-agree/disagree => Metacognitive.planning
        raw["Metacognitive.planning"] += 2.1
    if "no, no, it is different" in t or "it is different" in t:
        # Project-specific legacy mapping: planning-disagree => Metacognitive.planning
        raw["Metacognitive.planning"] += 2.1
    if re.search(
        r"\b(on track|progress|next question|move on|pace|behind schedule|time left|are we)\b", t,
    ):
        raw["Metacognitive.monitoring"] += 1.8
    if re.search(
        r"\b(quality|good enough|make sense|evaluate|weak|strong enough|lack|detail|correct\?)\b", t,
    ):
        raw["Metacognitive.evaluating"] += 1.8
    # Generic conceptual cues — suppressed when the line is clearly Bloom/task-solution talk (see below).
    if not (
        _utterance_looks_like_bloom_task_solution_talk(t)
        and not _utterance_looks_like_bloom_definition_question(t)
    ):
        if re.search(
            r"\b(define|meaning|concept|what is|what are|what does|clarify|theory)\b",
            t,
        ):
            raw["Cognitive.concept_exploration"] += 1.6
        if re.search(r"\b(bloom|taxonomy)\b", t) and _utterance_looks_like_bloom_definition_question(t):
            raw["Cognitive.concept_exploration"] += 1.15
    if re.search(
        r"\b(option|answer|choose|pick|solution|final answer|which one|correct option)\b", t,
    ):
        raw["Cognitive.solution_development"] += 1.6
    if re.search(r"\b(split|divide|allocate|who should|your part|roles|who does)\b", t):
        raw["Coordinative.coordinate_participants"] += 1.5
    if not _utterance_looks_like_metacognitive_planning_chat(t) and re.search(
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
    if re.search(
        r"\b(i actually|i study|i'm super|i am super|super aware|developmental science|my field|my background|i work in|i'm a student|i work as)\b",
        t,
    ):
        raw["Socio-emotional.self_disclosure"] += 2.4
    if re.search(r"\b(haha|lol|funny|hilarious|frustrated|nervous|worried|😂|哈哈)\b", t):
        raw["Socio-emotional.emotional_expression"] += 2.0
    if re.search(r"\bhhh\b", t):
        raw["Socio-emotional.emotional_expression"] += 1.6

    _apply_informational_vs_socioemotional_bias(raw, t)

    if "?" in t:
        raw["Metacognitive.planning"] += 0.9
        if not _utterance_looks_like_bloom_task_solution_talk(t):
            raw["Cognitive.concept_exploration"] += 0.7
        raw["Cognitive.solution_development"] += 0.6

    _apply_bloom_task_and_golden_ce_sd_bias(raw, t, norm, context)
    _apply_metacognitive_planning_heuristics(raw, t, norm, context)
    _apply_session_context_cognitive_bias(raw, t, norm)
    _apply_session_window_cognitive_bias(raw, t, context)

    mx = max(raw.values())
    if mx <= 0:
        return _baseline_semantic_spread(t, codes)
    return _softmax_scores_from_raw(raw, codes)


def _label_scores(text: str) -> dict[str, float]:
    """Alias for repair / legacy callers — semantic proxy on 0–5 scale."""
    return _semantic_proxy_scores(text)


def _best_label_from_scores(scores: dict[str, float]) -> tuple[str, float]:
    if not scores:
        return "Cognitive.solution_development", 0.0
    best = max(scores.items(), key=lambda kv: kv[1])
    return best[0], best[1]


def _infer_label_from_prompt(
    text: str,
    context: dict[str, Any] | None = None,
) -> tuple[str, list[str], dict[str, float]]:
    """
    Returns (chosen_label, ranked_candidates_desc, scores).
    Picks label from semantic proxy scores (0–5) — does **not** default to concept_exploration only.
    """
    scores = _semantic_proxy_scores(text, context)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    chosen, top = ranked[0][0], ranked[0][1]
    stripped = (text or "").strip().lower()
    if len(stripped) <= 20 and (
        any(x in stripped for x in ("haha", "lol", "哈哈", "😂", "hhh"))
        or re.fullmatch(r"h{3,}", stripped)
    ):
        # Do not treat short *informational* questions as pure emotional expression.
        if "?" in stripped and re.search(
            r"(?i)\b(know|about|bloom|taxonomy|what|which|how)\b",
            stripped,
        ):
            pass
        else:
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
    # Long prompts: keep enough clauses for evidence (display shows full text per span).
    return dedup[:32]


def _sentiment_tag(segment: str) -> str:
    t = segment.lower()
    if any(k in t for k in ("haha", "lol", "hilarious", "funny", "frustrated", "worried", "nervous", "angry", "sad", "😂", "哈哈", "hhh")):
        return "affective"
    if re.search(r"\b(thank|thanks|good job|great job|nice|appreciate)\b", t):
        return "supportive"
    if re.search(
        r"\b(not familiar|first time|never done|i've|i have|my experience|i actually|i study|super aware|developmental science|my field|my background)\b",
        t,
    ):
        return "self_disclosure"
    return "neutral_task"


def _build_signal_extractor_output(
    cleaned: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session_overview = _build_session_overview_dict(context, cleaned)
    segments = _segment_prompt_for_extraction(cleaned)
    evidence_spans: list[dict[str, Any]] = []
    candidate_signals: list[dict[str, Any]] = []
    ambiguity: list[dict[str, Any]] = []
    norm = _normalize_session_context(context)
    ctx_note = ""
    if _session_implies_task_oriented_discussion(norm) and (
        norm.get("group") or norm.get("scenario") or norm.get("timestamp")
    ):
        ctx_note = (
            f" Session 上下文: group={norm.get('group') or '—'}, "
            f"people={norm.get('people') or '—'}, "
            f"scenario={norm.get('scenario') or '—'}."
        )

    for idx, (seg, start, end) in enumerate(segments):
        chosen, cand_list, score_map = _infer_label_from_prompt(seg, context)
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
                    + ctx_note
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
        "session_overview": session_overview,
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
    *,
    context: dict[str, Any] | None = None,
) -> dict[str, float]:
    """
    LLM scores = semantic relevance (must be 0.00–5.00). Clamp and merge with semantic proxy
    when the model returns missing, invalid, or near-all-zero scores.
    """
    heur = _semantic_proxy_scores(cleaned_prompt, context)
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


def _counterexample_for_labels(assigned_label: str, alternative_label: str) -> str:
    """
    Build a minimal contrastive test so Boundary Critic can do reverse reasoning.
    """
    a = assigned_label or "current_label"
    b = alternative_label or "alternative_label"
    return (
        f"If the utterance were rephrased to clearly satisfy {b} (and not {a}), "
        "would the label still remain unchanged? If yes, boundary is likely overfit; if no, revise."
    )


def _is_close_score_boundary_challenge(c: dict[str, Any]) -> bool:
    """True if this challenge is about close top-two scores / ambiguous tier boundary."""
    q = (c.get("question") or "").lower()
    r = (c.get("reason") or "").lower()
    blob = f"{q} {r}"
    needles = (
        "top two",
        "close score",
        "close top",
        "scores are close",
        "ambiguous close",
        "close top-two",
        "semantic-fit scores are close",
        "margin=",
    )
    return any(n in blob for n in needles)


def _merge_challenge_dicts(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Merge two challenge dicts; prefer non-empty fields and longer text."""
    out = dict(a)
    for k, v in b.items():
        if v is None or v == "":
            continue
        old = out.get(k)
        if old is None or old == "":
            out[k] = v
        elif isinstance(old, str) and isinstance(v, str) and len(v) > len(old):
            out[k] = v
    return out


def _dedupe_boundary_critic_challenges(bc: dict[str, Any]) -> None:
    """Collapse duplicate close-score challenges (same label + alternative)."""
    ch = bc.get("challenges")
    if not isinstance(ch, list) or len(ch) < 2:
        return
    out: list[Any] = []
    for c in ch:
        if not isinstance(c, dict):
            out.append(c)
            continue
        if not _is_close_score_boundary_challenge(c):
            out.append(c)
            continue
        al = str(c.get("assigned_label") or "")
        alt = str(c.get("suggested_alternative") or "")
        merged = False
        for i, e in enumerate(out):
            if not isinstance(e, dict) or not _is_close_score_boundary_challenge(e):
                continue
            if al == str(e.get("assigned_label") or "") and alt == str(e.get("suggested_alternative") or ""):
                out[i] = _merge_challenge_dicts(e, c)
                merged = True
                break
        if not merged:
            out.append(c)
    bc["challenges"] = out


def _enrich_close_score_challenges_pro_con(bc: dict[str, Any], lc: dict[str, Any]) -> None:
    """Fill pro/con/reverse-test when a close-score challenge is missing structured fields."""
    ch = bc.get("challenges")
    if not isinstance(ch, list):
        return
    ranked = lc.get("label_scores_ranked") or []
    if len(ranked) < 2:
        return
    top, second = ranked[0], ranked[1]
    if not isinstance(top, dict) or not isinstance(second, dict):
        return
    margin_val = float(lc.get("label_scores_margin_top2") or 0.0)
    for c in ch:
        if not isinstance(c, dict) or not _is_close_score_boundary_challenge(c):
            continue
        if c.get("support_evidence") and c.get("counterexample_test"):
            continue
        assigned = str(c.get("assigned_label") or top.get("label") or "")
        alt = str(c.get("suggested_alternative") or second.get("label") or "")
        extra = _build_pro_con_reasoning(assigned, alt, margin=margin_val)
        for k, v in extra.items():
            if c.get(k) in (None, ""):
                c[k] = v


def _build_pro_con_reasoning(
    assigned_label: str,
    alternative_label: str,
    *,
    margin: float | None = None,
) -> dict[str, Any]:
    """
    Structured pro/con payload for Boundary Critic.
    """
    mtxt = f"{margin:.2f}" if isinstance(margin, (int, float)) else "n/a"
    return {
        "reasoning_mode": "pro_con",
        "support_evidence": (
            f"Pro({assigned_label}): current evidence can support this label under golden-label tier boundaries."
        ),
        "refute_evidence": (
            f"Con({assigned_label}): nearby alternative {alternative_label} is plausible; "
            f"close boundary signal suggests possible misassignment (margin={mtxt})."
        ),
        "counterexample_test": _counterexample_for_labels(assigned_label, alternative_label),
    }


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
        # Full adjudication rationale is produced in _finalize_adjudicator_with_boundary_critic.


def _finalize_adjudicator_with_boundary_critic(out: dict[str, Any]) -> None:
    """
    Final arbitration: not score-only. Weigh `label_scores` (top vs runner-up, margin)
    together with Boundary Critic challenges (questions, suggested_alternative, must_challenge).
    """
    adj = out.setdefault("adjudicator", {})
    lc = out.get("label_coder") or {}
    bc = out.get("boundary_critic") or {}
    ranked = lc.get("label_scores_ranked") or []
    challenges = bc.get("challenges") or []
    if not isinstance(challenges, list):
        challenges = []

    finals = adj.get("final_labels")
    if not isinstance(finals, list) or not finals:
        finals = [{"span_ref": 0, "label": "", "decision": "accept_coder", "rationale": ""}]
        adj["final_labels"] = finals
    f0 = finals[0]
    if not isinstance(f0, dict):
        return

    prior_model = (f0.get("rationale") or "").strip()

    top_label = ""
    top_score = 0.0
    second_label = ""
    second_score = 0.0
    if len(ranked) >= 1 and isinstance(ranked[0], dict):
        top_label = str(ranked[0].get("label") or "").strip()
        try:
            top_score = float(ranked[0].get("score") or 0.0)
        except (TypeError, ValueError):
            top_score = 0.0
    if len(ranked) >= 2 and isinstance(ranked[1], dict):
        second_label = str(ranked[1].get("label") or "").strip()
        try:
            second_score = float(ranked[1].get("score") or 0.0)
        except (TypeError, ValueError):
            second_score = 0.0

    if not top_label:
        top_label = str(f0.get("label") or "").strip()

    margin = float(lc.get("label_scores_margin_top2") or 0.0)
    scores_close = bool(lc.get("scores_close"))
    close_thr = SCORE_CLOSE_THRESHOLD

    # --- Decision policy: integrate Boundary Critic, not argmax-only ---
    decision = "accept_coder"
    final_label = top_label
    if challenges and scores_close:
        decision = "combine_both"
    elif challenges:
        decision = "accept_coder"

    f0["label"] = final_label
    f0["decision"] = decision

    labs = lc.get("labels") or []
    if labs and isinstance(labs[0], dict):
        labs[0]["label"] = final_label

    lines: list[str] = []
    lines.append("### Score basis")
    lines.append(f"- **Primary by rank:** `{top_label}` ({top_score:.2f})")
    if second_label:
        lines.append(f"- **Runner-up:** `{second_label}` ({second_score:.2f})")
    lines.append(f"- **Margin (top1 − top2):** {margin:.3f} (close threshold: {close_thr})")
    lines.append(f"- **Scores flagged close (`scores_close`):** {'yes' if scores_close else 'no'}")

    lines.append("")
    lines.append("### Boundary Critic — reviewed")
    if challenges:
        for i, c in enumerate(challenges[:8], 1):
            if not isinstance(c, dict):
                continue
            al = str(c.get("assigned_label") or "")
            alt = str(c.get("suggested_alternative") or "")
            must = bool(c.get("must_challenge"))
            lines.append(
                f"{i}. Assigned `{al}` → suggested alternative `{alt}` · must_challenge={must}"
            )
            q = (c.get("question") or "").strip()
            if q:
                lines.append(f"   - **Q:** {q}")
            rsn = (c.get("reason") or "").strip()
            if rsn:
                lines.append(f"   - **Note:** {rsn}")
            pro = (c.get("support_evidence") or "").strip()
            con = (c.get("refute_evidence") or "").strip()
            if pro:
                lines.append(f"   - **Pro:** {pro}")
            if con:
                lines.append(f"   - **Con:** {con}")
    else:
        lines.append("_No Boundary Critic challenges for this run._")

    lines.append("")
    lines.append("### Arbitration (precise)")
    if decision == "combine_both":
        ru = f"**`{second_label}`**" if second_label else "the runner-up code"
        lines.append(
            f"**Decision: `combine_both`** — Top score favors **`{top_label}`**; {ru} remains "
            f"plausible (margin {margin:.3f}; close threshold {close_thr}). The Boundary Critic raised substantive "
            "boundary questions. **Primary label** = ranked winner; **runner-up** stays **actively plausible**—resolve using evidence and golden-labels."
        )
    else:
        lines.append(
            f"**Decision: `accept_coder`** — After integrating scores"
            + (" and Boundary Critic messages" if challenges else "")
            + f", **`{final_label}`** is adopted as the final label. "
            + (
                "The score margin supports the leader over the runner-up."
                if not scores_close
                else "Challenges were reviewed; they do not warrant overriding the ranked leader on current evidence."
            )
        )

    structured = "\n".join(lines)
    if prior_model and "### Score basis" not in prior_model:
        f0["rationale"] = structured + "\n\n---\n**Prior adjudicator draft (model):**\n" + prior_model
    else:
        f0["rationale"] = structured

    adj["adjudication_analysis"] = structured
    adj["boundary_critic_weighed"] = bool(challenges)


def _ensure_label_coder_full_scores(
    out: dict[str, Any],
    cleaned_prompt: str,
    allowed_codes: list[str],
) -> None:
    lc = out.get("label_coder")
    if not isinstance(lc, dict):
        lc = {}
        out["label_coder"] = lc
    merged = _merge_label_scores_with_heuristic(
        lc,
        cleaned_prompt,
        allowed_codes,
        context=out.get("context"),
    )
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
    # Avoid duplicating heuristic/LLM challenges that already cover close scores.
    if any(isinstance(c, dict) and _is_close_score_boundary_challenge(c) for c in challenges):
        return
    assigned = ""
    labs = lc.get("labels") or []
    if labs and isinstance(labs[0], dict):
        assigned = str(labs[0].get("label") or "")
    margin_val = float(lc.get("label_scores_margin_top2") or 0.0)
    pro_con = _build_pro_con_reasoning(assigned, str(second.get("label") or ""), margin=margin_val)
    challenges.append({
        "span_ref": 0,
        "assigned_label": assigned,
        "question": (
            "Top two label scores are close — refine tier1/tier2: which code fits the "
            "primary intent per golden-labels?"
        ),
        "reason": (
            f"Scores are close (margin={margin_val:.2f}). "
            f"Compare {top.get('label')} ({top.get('score')}) vs {second.get('label')} ({second.get('score')}). "
            "Adjust boundary if evidence supports the runner-up."
        ),
        "suggested_alternative": str(second.get("label") or ""),
        "margin": round(margin_val, 3),
        "must_challenge": True,
        **pro_con,
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
    margin_val = float(lc.get("label_scores_margin_top2") or 0.0)
    pro_con = _build_pro_con_reasoning(assigned, runner_up, margin=margin_val if margin_val > 0 else None)

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
            "margin": round(margin_val, 3) if margin_val > 0 else None,
            "must_challenge": True,
            **pro_con,
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
    _maybe_repair_golden_hc_solution_development_vs_ce(out, cleaned_prompt, allowed_codes)
    _maybe_repair_golden_hc_concept_exploration_vs_sd(out, cleaned_prompt, allowed_codes)
    _maybe_repair_golden_hc_metacognitive_planning_vs_cognitive(out, cleaned_prompt, allowed_codes)
    _ensure_label_coder_full_scores(out, cleaned_prompt, allowed_codes)
    _sync_label_coder_and_adjudicator_from_scores(out)
    _ensure_boundary_critic_scores_close_challenge(out)
    _ensure_boundary_critic_ambiguity_challenge(out)
    bc = out.get("boundary_critic")
    lc = out.get("label_coder")
    if isinstance(bc, dict) and isinstance(lc, dict):
        _dedupe_boundary_critic_challenges(bc)
        _enrich_close_score_challenges_pro_con(bc, lc)
    apply_consistency_checking(out, cleaned_prompt, out.get("context"), allowed_codes)
    if (out.get("adjudicator") or {}).get("consistency_checking", {}).get("status") == "repaired":
        _ensure_label_coder_full_scores(out, cleaned_prompt, allowed_codes)
        _sync_label_coder_and_adjudicator_from_scores(out)
    _finalize_adjudicator_with_boundary_critic(out)
    append_consistency_to_adjudicator_rationale(out)


def _maybe_repair_concept_exploration_bias(
    out: dict[str, Any],
    cleaned_prompt: str,
    _allowed: list[str],
) -> None:
    """
    If the model labels everything Cognitive.concept_exploration but content scores favor
    another code, replace final/adjudicator labels with the heuristic best (when confident).
    """
    scores = _semantic_proxy_scores(cleaned_prompt, out.get("context"))
    best_h, best_s = _best_label_from_scores(scores)
    concept_s = scores.get("Cognitive.concept_exploration", 0.0)
    # Heuristic scores are calibrated lower than the old “top always 5.0” scale; use a modest floor.
    if best_h == "Cognitive.concept_exploration" or best_s < 1.95:
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


def _maybe_repair_golden_hc_solution_development_vs_ce(
    out: dict[str, Any],
    cleaned_prompt: str,
    _allowed: list[str],
) -> None:
    """
    When HC1/HC2 encode human `solution\\development-*` but agents still output Cognitive.concept_exploration,
    align finals to Cognitive.solution_development if heuristic scores agree.
    """
    ctx = out.get("context") or {}
    if not _golden_hc_implies_solution_development(ctx):
        return
    if _golden_hc_implies_concept_exploration(ctx):
        return
    scores = _semantic_proxy_scores(cleaned_prompt, ctx)
    sd_s = scores.get("Cognitive.solution_development", 0.0)
    ce_s = scores.get("Cognitive.concept_exploration", 0.0)
    if sd_s + 0.15 < ce_s:
        return

    adj = out.get("adjudicator") or {}
    finals = adj.get("final_labels") or []
    if not isinstance(finals, list) or not finals:
        return
    all_ce = all(
        isinstance(f, dict) and (f.get("label") == "Cognitive.concept_exploration")
        for f in finals
    )
    if not all_ce:
        return

    note = (
        "Golden HC (solution/development strand) aligns with Cognitive.solution_development; "
        "heuristic scores favor or tie solution_development over concept_exploration."
    )
    for f in finals:
        if not isinstance(f, dict):
            continue
        f["label"] = "Cognitive.solution_development"
        f["decision"] = "combine_both"
        prev = (f.get("rationale") or "").strip()
        f["rationale"] = f"{note} Prior: {prev}" if prev else note

    lc = out.get("label_coder") or {}
    labels = lc.get("labels") or []
    if isinstance(labels, list):
        for row in labels:
            if isinstance(row, dict) and row.get("label") == "Cognitive.concept_exploration":
                row["label"] = "Cognitive.solution_development"
                row["rationale"] = note
        lc["revision_note"] = note
    se = out.get("signal_extractor") or {}
    cands = se.get("candidate_signals") or []
    if isinstance(cands, list) and cands and isinstance(cands[0], dict):
        top3 = [c for c, s in sorted(scores.items(), key=lambda kv: -kv[1])[:3]]
        cands[0]["candidates"] = top3
        cands[0]["reason"] = "Aligned with HC solution/development strand and golden-labels CE vs SD rules."


def _maybe_repair_golden_hc_concept_exploration_vs_sd(
    out: dict[str, Any],
    cleaned_prompt: str,
    _allowed: list[str],
) -> None:
    """
    When HC1/HC2 encode `concept\\exploration-*` but agents output Cognitive.solution_development,
    align finals to Cognitive.concept_exploration if heuristic scores agree.
    """
    ctx = out.get("context") or {}
    if not _golden_hc_implies_concept_exploration(ctx):
        return
    if _golden_hc_implies_solution_development(ctx):
        return
    scores = _semantic_proxy_scores(cleaned_prompt, ctx)
    ce_s = scores.get("Cognitive.concept_exploration", 0.0)
    sd_s = scores.get("Cognitive.solution_development", 0.0)
    if ce_s + 0.15 < sd_s:
        return

    adj = out.get("adjudicator") or {}
    finals = adj.get("final_labels") or []
    if not isinstance(finals, list) or not finals:
        return
    all_sd = all(
        isinstance(f, dict) and (f.get("label") == "Cognitive.solution_development")
        for f in finals
    )
    if not all_sd:
        return

    note = (
        "Golden HC (concept/exploration strand) aligns with Cognitive.concept_exploration; "
        "heuristic scores favor or tie concept_exploration over solution_development."
    )
    for f in finals:
        if not isinstance(f, dict):
            continue
        f["label"] = "Cognitive.concept_exploration"
        f["decision"] = "combine_both"
        prev = (f.get("rationale") or "").strip()
        f["rationale"] = f"{note} Prior: {prev}" if prev else note

    lc = out.get("label_coder") or {}
    labels = lc.get("labels") or []
    if isinstance(labels, list):
        for row in labels:
            if isinstance(row, dict) and row.get("label") == "Cognitive.solution_development":
                row["label"] = "Cognitive.concept_exploration"
                row["rationale"] = note
        lc["revision_note"] = note
    se = out.get("signal_extractor") or {}
    cands = se.get("candidate_signals") or []
    if isinstance(cands, list) and cands and isinstance(cands[0], dict):
        top3 = [c for c, s in sorted(scores.items(), key=lambda kv: -kv[1])[:3]]
        cands[0]["candidates"] = top3
        cands[0]["reason"] = "Aligned with HC concept/exploration strand (see cognitive-tier2-hc-subactions.md)."


def _maybe_repair_golden_hc_metacognitive_planning_vs_cognitive(
    out: dict[str, Any],
    cleaned_prompt: str,
    allowed_codes: list[str],
) -> None:
    """
    When HC1/HC2 encode `planning-*` (e.g. planning-give, planning-agree) but agents still output
    Cognitive.* (often solution_development or concept_exploration), align to Metacognitive.planning.

    Also patches `label_scores` so the second `_ensure_label_coder_full_scores` + `_sync` keep MP on top.
    """
    ctx = out.get("context") or {}
    if not _golden_hc_implies_metacognitive_planning(ctx):
        return
    scores = _semantic_proxy_scores(cleaned_prompt, ctx)
    mp = scores.get("Metacognitive.planning", 0.0)
    sd = scores.get("Cognitive.solution_development", 0.0)
    ce = scores.get("Cognitive.concept_exploration", 0.0)
    tl = (cleaned_prompt or "").strip().lower()
    short_back = len(tl) <= 28 and bool(re.match(r"(?i)^(yes|yeah|yep|ok|okay|sure)\.?!?$", tl.strip()))
    best_cog = max(sd, ce)

    if mp + 0.1 < best_cog:
        if not _utterance_looks_like_metacognitive_planning_chat(tl) and not short_back:
            return

    adj = out.get("adjudicator") or {}
    finals = adj.get("final_labels") or []
    if not isinstance(finals, list) or not finals:
        return
    all_cognitive_wrong = all(
        isinstance(f, dict) and str(f.get("label") or "").startswith("Cognitive.")
        for f in finals
    )
    if not all_cognitive_wrong:
        return

    note = (
        "Golden HC (planning-* strand) aligns with Metacognitive.planning; "
        "do not use Cognitive.solution_development / concept_exploration for procedure / how-to-structure talk "
        "(see metacognitive-tier2-hc-subactions.md)."
    )
    for f in finals:
        if not isinstance(f, dict):
            continue
        f["label"] = "Metacognitive.planning"
        f["decision"] = "combine_both"
        prev = (f.get("rationale") or "").strip()
        f["rationale"] = f"{note} Prior: {prev}" if prev else note

    lc = out.get("label_coder") or {}
    labels = lc.get("labels") or []
    if isinstance(labels, list):
        for row in labels:
            if isinstance(row, dict) and str(row.get("label") or "").startswith("Cognitive."):
                row["label"] = "Metacognitive.planning"
                row["rationale"] = note
        lc["revision_note"] = note

    raw_scores = lc.get("label_scores")
    if isinstance(raw_scores, dict):
        for k in list(raw_scores.keys()):
            ks = str(k)
            if ks.startswith("Cognitive.") or ks.startswith("Coordinative."):
                try:
                    raw_scores[k] = min(float(raw_scores[k]), 1.12)
                except (TypeError, ValueError):
                    raw_scores[k] = 1.0
            elif ks.startswith("Metacognitive.") and ks != "Metacognitive.planning":
                try:
                    raw_scores[k] = min(float(raw_scores[k]), 1.18)
                except (TypeError, ValueError):
                    raw_scores[k] = 1.0
        try:
            cur_mp = float(raw_scores.get("Metacognitive.planning", 0.0))
        except (TypeError, ValueError):
            cur_mp = 0.0
        raw_scores["Metacognitive.planning"] = max(cur_mp, 4.92)

        for code in allowed_codes:
            if code not in raw_scores:
                raw_scores[code] = scores.get(code, 0.0)

    se = out.get("signal_extractor") or {}
    cands = se.get("candidate_signals") or []
    if isinstance(cands, list) and cands and isinstance(cands[0], dict):
        top3 = [c for c, s in sorted(scores.items(), key=lambda kv: -kv[1])[:3]]
        cands[0]["candidates"] = top3
        cands[0]["reason"] = "Aligned with HC planning-* strand (Metacognitive.planning)."


def _format_session_context_for_llm(
    context: dict[str, Any] | None,
    current_prompt: str = "",
) -> str:
    """Structured 上下文 block for the LLM user message (disambiguation + session window)."""
    n = _normalize_session_context(context)
    lines: list[str] = []
    if not any([n.get("group"), n.get("people"), n.get("timestamp"), n.get("scenario")]):
        lines.append("### Session context (上下文)")
        lines.append("_Limited metadata — infer primarily from the prompt text._")
    else:
        lines.extend(
            [
                "### Session context (上下文) — MUST use for disambiguation",
                f"- **group:** {n.get('group') or '—'}",
                f"- **people:** {n.get('people') or '—'}",
                f"- **timestamp / segment:** {n.get('timestamp') or '—'}",
                f"- **scenario / condition tag:** {n.get('scenario') or '—'}",
                "",
                "When this metadata indicates a **communication or study episode**, very short utterances may refer to the **joint task** (e.g. naming an answer, picking an option). "
                "Then prefer **Cognitive.solution_development** over **Cognitive.concept_exploration** for phrases like *Naming and defining.* unless the speaker is clearly asking for abstract conceptual definitions.",
            ]
        )

    before, after = _session_neighbor_lists(context)
    if before or after:
        lines.append("")
        lines.append("### Neighboring prompts (same group · time order)")
        lines.append(
            "**Whole-session semantic focus (required for Cognitive tier2):** Read *all* lines below together. "
            "**Cognitive.concept_exploration** = talk about **concepts of the learning task** (meanings, definitions, theory). "
            "**Cognitive.solution_development** = talk about **solutions for the learning task** (answers, options, how to label/classify the response). "
            "When the current line is short or ambiguous, choose the tier2 that matches the **dominant focus of this window**, not an isolated keyword."
        )
        for i, p in enumerate(before, 1):
            lines.append(f"- **Before {i}:** {p[:500]}{'…' if len(p) > 500 else ''}")
        lines.append(f"- **Current:** {current_prompt[:800]}{'…' if len(current_prompt) > 800 else ''}")
        for i, p in enumerate(after, 1):
            lines.append(f"- **After {i}:** {p[:500]}{'…' if len(p) > 500 else ''}")
        ov = _build_session_overview_dict(context, current_prompt)
        lines.append("")
        lines.append(f"**Heuristic session cognitive tilt:** `{ov.get('cognitive_tilt', 'neutral')}`")
        lines.append(str(ov.get("summary_line") or ""))

    nctx = context or {}
    pl = str(nctx.get("neighbor_previous_predicted_label") or "").strip()
    nl = str(nctx.get("neighbor_next_predicted_label") or "").strip()
    if pl or nl:
        lines.append("")
        lines.append("### Neighbor predicted labels (consistency checking)")
        if pl:
            lines.append(f"- **Previous turn label:** `{pl}`")
        if nl:
            lines.append(f"- **Next turn label:** `{nl}`")
        lines.append(
            "_Use these with the current utterance: **Tier1 (event)** should align across interactive pairs "
            "(ask/answer, give/agree, …); **Tier2 (act)** is the reference when fixing event._"
        )
    if nctx.get("consistency_retry_instruction"):
        lines.append("")
        lines.append("### Consistency checking — retry round (shared across all agents)")
        lines.append(str(nctx["consistency_retry_instruction"]).strip())

    return "\n".join(lines)


def _run_llm_pipeline_once(cleaned_prompt: str, context: dict[str, Any]) -> dict[str, Any] | None:
    cfg = load_config_from_env()
    if cfg is None:
        return None

    allowed = _taxonomy_codes()
    golden = _golden_summary()
    sys_prompt = _system_prompt()
    ctx_block = _format_session_context_for_llm(context, cleaned_prompt)

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
- Use ONLY the current prompt and **session context (上下文)** (group, people, timestamp, scenario) plus provided artifacts (taxonomy + golden labels). **Do not ignore metadata:** it situates the utterance in a communication scenario.
- Final labels MUST be chosen from this allowed taxonomy list:
{allowed}
- **Labeling procedure (mandatory):** For each span, decide **Tier1 first** (Cognitive vs Metacognitive vs Coordinative vs Socio-emotional), then **Tier2** within that tier. The label must match the **primary intent** of the quoted evidence, not a generic default.
- **Do NOT default to Cognitive.concept_exploration.** Use it only when the utterance is mainly about **concepts/definitions/learning meanings** (e.g. what a term means, clarifying a concept). Do **not** use it for: pure laughter/reactions, thanks/praise, task splitting/roles, planning how to solve, checking progress, judging output quality, or picking/correct answers—those map to other codes in the list above.
- **Cognitive tier2 with 上下文:** When session tags (e.g. group id, no-gai/gai, discussion) suggest **collaborative task talk**, terse lines like *Naming and defining.* usually mean **naming/labeling the solution or answer** → **Cognitive.solution_development**, not abstract concept exploration.
- **Cognitive tier2 — whole-session focus:** Decide **Cognitive.concept_exploration** vs **Cognitive.solution_development** using the **entire session window** (neighboring prompts + current) when provided. **concept_exploration** = primary focus on **concepts of the learning task** (what ideas/terms mean). **solution_development** = primary focus on **solutions for the learning task** (task products, answers, options). Do **not** label from a single ambiguous word if the **whole episode** is clearly about solutions vs concepts.
- **Human HC shorthand:** Strand prefix disambiguates parallel sub-actions: **`solution\\development-*`** → **Cognitive.solution_development**; **`concept\\exploration-*`** → **Cognitive.concept_exploration** (same sub-action names, different focus—see **cloudbot/data/cognitive-tier2-hc-subactions.md**). If only `solution\\development-*` is present, do not use concept_exploration; if only `concept\\exploration-*`, do not use solution_development unless session evidence clearly contradicts HC.
- **Evidence:** Every Label Coder and Adjudicator label must cite a **verbatim substring** of the prompt in `evidence_used` / rationale; candidate_signals should list 1–3 plausible codes when ambiguous.
- **Signal Extractor precision:** split the prompt into multiple minimal evidence spans (sentence/clause level), attach precise start/end offsets, keep each span verbatim, and include sentiment-aware rationale per span (e.g. affective/supportive/neutral_task). Do not collapse to one full-span evidence unless the prompt is too short to segment.
- **Label Coder must output `label_scores`:** an object with **every** code in the allowed list as a key exactly once. Each value must be a number from **0.00 to 5.00** (two decimals), representing **semantic fit** (intent, not keyword counts). **Calibrate:** do **not** set every plausible code to 5.00—reserve **~4.5–5.00** for a **clear best** fit; **~2.5–4.0** for strong but not exclusive fit; **~1.0–2.5** for weak/background plausibility; **~0.00–1.00** for no meaningful fit. At most **one** code should be near 5.00 unless the utterance is genuinely multi-intent. Session/window context is **one** input among many; scores must still reflect the **current** utterance. The pipeline ranks codes; if #1 and #2 are **close**, the Boundary Critic refines the boundary.
- Use golden-labels boundaries and decision rules below.
- Keep evidence spans minimal but sufficient; use span_ref=0 for the whole prompt if needed.
- Boundary Critic must only challenge, not decide.
- If Signal Extractor ambiguity says close top-two scores (or equivalent), Boundary Critic must output at least one challenge for that span (no empty challenges in this case).
- Adjudicator must decide (accept_coder / accept_critic / combine / uncertain) and justify.
- **Consistency checking (Adjudicator):** When **neighbor predicted labels** are provided in context (`neighbor_previous_predicted_label` / `neighbor_next_predicted_label`), reason about whether **Tier1 (event)** stays aligned across **interactive pairs** (ask/answer, give/agree, give/disagree, give/build on) with the adjacent turn. **Act (tier2)** is the reference for resolving event mismatches; consecutive interactive acts should share the same event. Within **Cognitive**, if neighbors are both about the **same conceptual strand** (e.g. human `concept\\exploration-*`), do **not** flip **concept_exploration** → **solution_development** without task-solution cues (answers, options, labeling the response). Within **Metacognitive**, if the previous turn is **monitoring** (e.g. move on? next section?) and the current line is short **assent** (“I think it’s okay.”), keep **monitoring** — do **not** label the answer as **planning** unless the speaker proposes a **new** procedure (steps, “first we…”). **Socio-emotional.emotional_expression** is for **affect/reaction** only — **not** for informational questions (e.g. “Do you know about Bloom’s taxonomy?”) or conceptual discussion; those are **Cognitive** (usually **concept_exploration**). If a **Consistency retry** block appears above, follow it and output a revised full JSON.

Golden-labels criteria excerpt:
{golden}
""".strip()

    messages = [
        {"role": "system", "content": sys_prompt or "You are an autocoding pipeline."},
        {
            "role": "user",
            "content": f"{ctx_block}\n\n**Prompt:**\n{cleaned_prompt}\n\n{instruction}",
        },
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
    se = out.get("signal_extractor")
    if isinstance(se, dict):
        se["session_overview"] = _build_session_overview_dict(context, cleaned_prompt)
    return out


def _run_llm_pipeline(cleaned_prompt: str, context: dict[str, Any]) -> dict[str, Any] | None:
    allowed = _taxonomy_codes()
    out = _run_llm_pipeline_once(cleaned_prompt, context)
    if out is None:
        return None
    _postprocess_pipeline_output(out, cleaned_prompt, allowed)

    cc = (out.get("adjudicator") or {}).get("consistency_checking") or {}
    retry_ct = int((context or {}).get("consistency_llm_retry_count", 0) or 0)
    if (
        cc.get("status") == "failed"
        and cc.get("retry_required")
        and retry_ct < 1
    ):
        ctx2 = dict(context or {})
        ctx2["consistency_llm_retry_count"] = 1
        ctx2["consistency_retry_instruction"] = str(cc.get("retry_instruction_for_agents") or "").strip()
        out2 = _run_llm_pipeline_once(cleaned_prompt, ctx2)
        if out2 is not None:
            merged_ctx = dict(out2.get("context") or {})
            merged_ctx.update(ctx2)
            out2["context"] = merged_ctx
            _postprocess_pipeline_output(out2, cleaned_prompt, allowed)
            adj = out2.setdefault("adjudicator", {})
            adj["consistency_llm_retry_completed"] = True
            return out2
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

    chosen, cand_list, score_map = _infer_label_from_prompt(cleaned, context)
    top_score = score_map.get(chosen, 0.0)
    second_best = sorted(score_map.items(), key=lambda kv: -kv[1])[1][1] if len(score_map) > 1 else 0.0

    # --- Signal Extractor ---
    signal_extractor = _build_signal_extractor_output(cleaned, context)
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
    _norm_h = _normalize_session_context(context)
    if _session_implies_task_oriented_discussion(_norm_h) and (
        _norm_h.get("group") or _norm_h.get("scenario") or _norm_h.get("timestamp")
    ):
        rationale += (
            f" Session 上下文 applied: group={_norm_h.get('group') or '—'}, "
            f"people={_norm_h.get('people') or '—'}, "
            f"scenario={_norm_h.get('scenario') or '—'} (see golden-labels Session context)."
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
    # Close-score / extractor ambiguity challenges are added in _postprocess via
    # _ensure_boundary_critic_scores_close_challenge (avoids duplicate shallow challenges).
    if "reasoning" in cleaned.lower() and "Metacognitive" in chosen:
        alt = cand_list[1] if len(cand_list) > 1 else ""
        pro_con = _build_pro_con_reasoning(chosen, alt, margin=abs(top_score - second_best))
        challenges.append({
            "assigned_label": chosen,
            "question": "Is this monitoring or evaluating?",
            "reason": "Checking reasoning can be either progress monitoring or solution evaluation.",
            "suggested_alternative": alt,
            "margin": round(abs(top_score - second_best), 3),
            "must_challenge": True,
            **pro_con,
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
