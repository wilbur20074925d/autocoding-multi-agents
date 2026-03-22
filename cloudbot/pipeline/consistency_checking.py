"""
Adjudicator — Consistency checking (event–act alignment across adjacent turns).

Theory (Ethnography of Communication): consecutive interactive acts (ask/answer,
give/agree, give/disagree, give/build on) should share the same *event* (Tier1).
Act (tier2) prediction is treated as more reliable than event; we align Tier1 when
neighbors form an interactive pair but Tier1 differs.

Within **Cognitive**, consecutive turns on the same conceptual thread should not
flip **concept_exploration** ↔ **solution_development** without cause: when the
previous turn is **concept_exploration** and the current line continues that
strand (or HC `concept\\exploration-*`), we align the current label to
**concept_exploration** rather than **solution_development**.

Within **Metacognitive**, when the previous turn is **monitoring** (next step /
progress) and the current line is mislabeled **planning** though it only assents
to that monitoring question, we align to **monitoring**.

See golden-labels.md and metacognitive-tier2-hc-subactions.md.
"""

from __future__ import annotations

import re
from typing import Any

# Communicative sequence patterns (group discussion)
_PAIR_HINTS = (
    "ask_answer",  # ? … + short reply
    "give_agree",  # longer move + yes/ok/agree
    "give_disagree",
    "give_build_on",
    "short_followup",  # very short turn after substantive neighbor
)


def parse_event_act(full_label: str) -> tuple[str, str]:
    """Split `Tier1.tier2` into event (tier1 name) and act (tier2)."""
    s = (full_label or "").strip()
    if "." not in s:
        return ("", "")
    parts = s.split(".", 1)
    return (parts[0].strip(), parts[1].strip())


def _is_short_backchannel(text: str) -> bool:
    t = (text or "").strip()
    return len(t) <= 36 and bool(
        re.match(r"(?i)^(yes|yeah|yep|ok|okay|sure|no|nope|nah)\.?!?$", t)
    )


def _looks_like_question(text: str) -> bool:
    t = (text or "").strip()
    return "?" in t or bool(re.search(r"(?i)^(what|how|why|when|where|who|which|do |does |did |is |are |can |could |would |should )\b", t))


def _interactive_pair_type(
    text_a: str,
    text_b: str,
    label_a: str,
    label_b: str,
) -> str | None:
    """
    Heuristic: do (a,b) in order look like an interactive sequence needing same event?
    Returns a hint key or None.
    """
    la, lb = (text_a or "").strip(), (text_b or "").strip()
    if not la or not lb:
        return None
    # ask → answer
    if _looks_like_question(la) and len(lb) <= 120 and not _looks_like_question(lb):
        return "ask_answer"
    # give → agree / short assent (neighbor order: current → next)
    if len(la) >= 24 and _is_short_backchannel(lb):
        return "give_agree"
    # disagree stub
    if len(la) >= 20 and len(lb) <= 80 and re.search(
        r"(?i)\b(disagree|not sure|i don'?t think|actually no)\b",
        lb,
    ):
        return "give_disagree"
    # build-on
    if len(la) >= 20 and len(lb) >= 20 and re.search(r"(?i)\b(and (also|i )?|building on|to add)\b", lb[:60]):
        return "give_build_on"
    # HC-like planning-give + planning-agree (tier2 both planning under same tier1 if consistent)
    ea_a, ta_a = parse_event_act(label_a)
    ea_b, ta_b = parse_event_act(label_b)
    if ta_a == "planning" and ta_b == "planning" and ea_a != ea_b:
        return "planning_pair"
    if _is_short_backchannel(lb) and len(la) > len(lb) + 10:
        return "short_followup"
    return None


def _tier2_valid_under_event(event: str, tier2: str, allowed_codes: set[str]) -> bool:
    cand = f"{event}.{tier2}"
    return cand in allowed_codes


def _map_label_to_event_preserving_act(
    source_label: str,
    target_event: str,
    allowed_codes: set[str],
) -> str | None:
    """Pick a full code under target_event using act (tier2) from source when possible."""
    _e, act = parse_event_act(source_label)
    if act and _tier2_valid_under_event(target_event, act, allowed_codes):
        return f"{target_event}.{act}"
    # Fallback: first code in taxonomy order for that event (caller passes ordered list)
    return None


def _pick_fallback_for_event(
    target_event: str,
    allowed_codes: list[str],
    *,
    pair_kind: str | None = None,
) -> str | None:
    """Prefer Metacognitive.planning for give/agree-style sequences when mapping fails."""
    if target_event == "Metacognitive" and pair_kind in (
        "give_agree",
        "short_followup",
        "planning_pair",
        "ask_answer",
    ):
        if "Metacognitive.planning" in allowed_codes:
            return "Metacognitive.planning"
    for c in allowed_codes:
        if c.startswith(f"{target_event}."):
            return c
    return None


def _hc_implies_concept_exploration_strand(context: dict[str, Any] | None) -> bool:
    """Human CSV `concept\\exploration-*` or concept/exploration-*."""
    if not context:
        return False
    for key in ("HC1", "HC2", "hc1", "hc2", "gold_label", "label_gold"):
        v = str(context.get(key) or "").lower().replace("\\", "/")
        if "concept" in v and "exploration" in v:
            return True
    return False


def _hc_implies_solution_development_strand(context: dict[str, Any] | None) -> bool:
    if not context:
        return False
    for key in ("HC1", "HC2", "hc1", "hc2", "gold_label", "label_gold"):
        v = str(context.get(key) or "").lower().replace("\\", "/")
        if "solution" in v and "development" in v:
            return True
    return False


def _strong_solution_development_cues(text: str) -> bool:
    """Phrases that justify Cognitive.solution_development over concept_exploration."""
    tl = (text or "").strip().lower()
    return bool(
        re.search(
            r"(?i)\b(option|options|answer is|the answer|choose |pick |correct option|which one is|final answer|"
            r"label (the |our |this )?response|naming and defining|for understanding i|differentiating and)\b",
            tl,
        )
    )


def _hc_implies_monitoring_strand(context: dict[str, Any] | None) -> bool:
    """Human CSV e.g. `monitoring-ask`, `monitoring/agree`, `monitoring-answer`."""
    if not context:
        return False
    for key in ("HC1", "HC2", "hc1", "hc2", "gold_label", "label_gold"):
        v = str(context.get(key) or "").lower().replace("\\", "/")
        if re.search(r"(?i)\bmonitoring[-/]", v) or v.strip().startswith("monitoring-"):
            return True
    return False


def _hc_implies_planning_strand(context: dict[str, Any] | None) -> bool:
    """Human CSV `planning-*` → Metacognitive.planning."""
    if not context:
        return False
    for key in ("HC1", "HC2", "hc1", "hc2", "gold_label", "label_gold"):
        v = str(context.get(key) or "").lower().replace("\\", "/")
        if re.search(r"(?i)\bplanning[-/]", v) or v.strip().startswith("planning-"):
            return True
    return False


def _prev_prompt_suggests_monitoring_question(prev_prompt: str) -> bool:
    """Prior turn asks about progress, next step, or moving on (monitoring), not procedure planning."""
    pl = (prev_prompt or "").strip().lower()
    if not pl:
        return False
    if re.search(
        r"(?i)\b(move on|next question|next part|multiple[- ]choice|multiple choice|"
        r"ready to|are we ready|should we|do you want to|want to go|want to move|"
        r"continue (to|with)|skip to|time to|shall we|okay to|proceed to)\b",
        pl,
    ):
        return True
    if "?" in pl and re.search(
        r"(?i)\b(on (the )?right track|pace|progress|finish|done yet|keep going|same page)\b",
        pl,
    ):
        return True
    return False


def _current_looks_like_monitoring_assent(current_text: str) -> bool:
    """Short agreement / okay to proceed — answers a monitoring question, not a new plan."""
    t = (current_text or "").strip().lower()
    if not t or len(t) > 120:
        return False
    if re.match(
        r"(?i)^(yes|yeah|yep|ok|okay|sure|fine|good|sounds good|let's go|let us go)\s*\.?!?$",
        t,
    ):
        return True
    if re.search(r"(?i)\b(i think )?it'?s okay\b", t) and "?" not in t:
        return True
    if re.search(r"(?i)\b(i think (that |it )?)?(is |')?ok(ay)?\b", t) and len(t) < 90 and "?" not in t:
        return True
    if re.search(r"(?i)\b(that sounds|sounds )?(good|fine|okay)\b", t) and len(t) < 100:
        return True
    return False


def _utterance_looks_like_planning_proposal(text: str) -> bool:
    """Explicit procedure / structure proposal — do not recode as monitoring."""
    tl = (text or "").strip().lower()
    return bool(
        re.search(
            r"(?i)\b(we should first|we can first|let's first|first we|list the|in bullet|"
            r"how should we solve|what steps|strategy|approach|procedure for)\b",
            tl,
        )
    )


def _should_align_monitoring_over_planning(
    prev_prompt: str,
    current_text: str,
    context: dict[str, Any] | None,
) -> bool:
    """
    Previous line was monitoring; current mislabeled as planning — align when 上下文 is
    assent to progress/next-step, not a new plan.
    """
    if _utterance_looks_like_planning_proposal(current_text):
        return False
    if _hc_implies_monitoring_strand(context) and not _hc_implies_planning_strand(context):
        return True
    if _hc_implies_planning_strand(context) and not _hc_implies_monitoring_strand(context):
        return False
    if _hc_implies_planning_strand(context) and _hc_implies_monitoring_strand(context):
        return bool(
            _prev_prompt_suggests_monitoring_question(prev_prompt)
            and _current_looks_like_monitoring_assent(current_text)
        )
    if _prev_prompt_suggests_monitoring_question(prev_prompt) and _current_looks_like_monitoring_assent(current_text):
        return True
    return False


def _should_align_current_ce_over_sd(
    current_text: str,
    context: dict[str, Any] | None,
) -> bool:
    """
    Previous neighbor is concept_exploration but current was mislabeled as solution_development:
    align to CE when 上下文 / HC / discourse indicate the same conceptual strand.
    """
    if _hc_implies_concept_exploration_strand(context) and not _hc_implies_solution_development_strand(context):
        return True
    if _hc_implies_concept_exploration_strand(context) and _hc_implies_solution_development_strand(context):
        return False
    tl = (current_text or "").strip().lower()
    if _strong_solution_development_cues(current_text):
        return False
    if re.search(
        r"(?i)^\s*(do you (all )?know|have you heard|are you familiar|did you learn|what is|what are|what does|"
        r"can you explain|tell me about|define |explain )\b",
        tl,
    ):
        return True
    if re.search(r"(?i)\b(bloom|taxonomy|metacognitive)\b", tl) and "?" in tl:
        return True
    if re.search(r"(?i)^(and |so |the goal |it seems|this (is|means)|right\?)", tl[:140]):
        return True
    if re.search(
        r"(?i)\b(process of how|goal for teaching|teaching is|pedagog|students learn)\b",
        tl,
    ):
        return True
    return False


def _mutate_output_to_label(
    out: dict[str, Any],
    new_label: str,
    allowed_codes: list[str],
    note: str,
) -> None:
    adj = out.setdefault("adjudicator", {})
    finals = adj.get("final_labels")
    if isinstance(finals, list) and finals and isinstance(finals[0], dict):
        old = finals[0].get("label")
        finals[0]["label"] = new_label
        finals[0]["decision"] = "combine_both"
        finals[0]["rationale"] = note + "\n\n" + str(finals[0].get("rationale") or "")
    lc = out.get("label_coder") or {}
    labs = lc.get("labels")
    if isinstance(labs, list) and labs and isinstance(labs[0], dict):
        labs[0]["label"] = new_label
        labs[0]["rationale"] = (labs[0].get("rationale") or "") + (" | " + note if note else "")
    _bump_scores_for_label(lc, new_label, allowed_codes)


def _repair_cognitive_ce_vs_sd_if_needed(
    out: dict[str, Any],
    cleaned_prompt: str,
    context: dict[str, Any] | None,
    prev_predicted_label: str,
    curr_predicted_label: str,
    allowed_codes: list[str],
    prev_prompt: str = "",
) -> bool:
    """
    When previous turn is Cognitive.concept_exploration and current is Cognitive.solution_development
    but the same conceptual thread continues, repair current to concept_exploration.
    """
    e1, a1 = parse_event_act(prev_predicted_label)
    e2, a2 = parse_event_act(curr_predicted_label)
    if e1 != "Cognitive" or e2 != "Cognitive":
        return False
    if a1 != "concept_exploration" or a2 != "solution_development":
        return False
    if not _should_align_current_ce_over_sd(cleaned_prompt, context):
        return False
    new_label = "Cognitive.concept_exploration"
    note = (
        "Consistency checking (Cognitive strand): previous turn is **concept_exploration**; "
        "current line continues the same conceptual thread — align to **Cognitive.concept_exploration** "
        "(not solution_development). See golden-labels CE vs SD."
    )
    _mutate_output_to_label(out, new_label, allowed_codes, note)
    adj = out.setdefault("adjudicator", {})
    adj["consistency_checking"] = {
        "status": "repaired",
        "pair_role": "previous_vs_current",
        "phase": "cognitive_ce_sd_alignment",
        "interactive_sequence": "same_conceptual_strand",
        "event_mismatch": False,
        "resolution": note,
        "repaired_label": new_label,
        "retry_required": False,
        "current_code": {
            "full": new_label,
            "event": "Cognitive",
            "act": "concept_exploration",
        },
        "neighbor_code": {
            "full": prev_predicted_label,
            "event": e1,
            "act": a1,
        },
        "current_sentence_preview": (cleaned_prompt[:220] + "…") if len(cleaned_prompt) > 220 else cleaned_prompt,
        "neighbor_sentence_preview": (
            (prev_prompt[:220] + "…") if len(prev_prompt) > 220 else prev_prompt
        )
        if prev_prompt
        else "—",
    }
    return True


def _repair_metacognitive_monitoring_vs_planning_if_needed(
    out: dict[str, Any],
    cleaned_prompt: str,
    context: dict[str, Any] | None,
    prev_predicted_label: str,
    curr_predicted_label: str,
    allowed_codes: list[str],
    prev_prompt: str = "",
) -> bool:
    """
    Previous turn is Metacognitive.monitoring (e.g. move on? next section?); current mislabeled
    as Metacognitive.planning though it assents / responds in the same monitoring thread.
    """
    e1, a1 = parse_event_act(prev_predicted_label)
    e2, a2 = parse_event_act(curr_predicted_label)
    if e1 != "Metacognitive" or e2 != "Metacognitive":
        return False
    if a1 != "monitoring" or a2 != "planning":
        return False
    if not _should_align_monitoring_over_planning(prev_prompt, cleaned_prompt, context):
        return False
    new_label = "Metacognitive.monitoring"
    note = (
        "Consistency checking (Metacognitive strand): previous turn is **monitoring** (progress / next step); "
        "current line answers or assents in the same thread — align to **Metacognitive.monitoring** "
        "(not planning). See golden-labels planning vs monitoring."
    )
    _mutate_output_to_label(out, new_label, allowed_codes, note)
    adj = out.setdefault("adjudicator", {})
    adj["consistency_checking"] = {
        "status": "repaired",
        "pair_role": "previous_vs_current",
        "phase": "metacognitive_monitoring_planning_alignment",
        "interactive_sequence": "monitoring_ask_assent",
        "event_mismatch": False,
        "resolution": note,
        "repaired_label": new_label,
        "retry_required": False,
        "current_code": {
            "full": new_label,
            "event": "Metacognitive",
            "act": "monitoring",
        },
        "neighbor_code": {
            "full": prev_predicted_label,
            "event": e1,
            "act": a1,
        },
        "current_sentence_preview": (cleaned_prompt[:220] + "…") if len(cleaned_prompt) > 220 else cleaned_prompt,
        "neighbor_sentence_preview": (
            (prev_prompt[:220] + "…") if len(prev_prompt) > 220 else prev_prompt
        )
        if prev_prompt
        else "—",
    }
    return True


def _bump_scores_for_label(lc: dict[str, Any], new_label: str, allowed_codes: list[str]) -> None:
    """Force label_scores so ranked winner is new_label (same idea as golden HC repairs)."""
    raw = lc.get("label_scores")
    if not isinstance(raw, dict):
        return
    for k in list(raw.keys()):
        ks = str(k)
        if ks == new_label:
            continue
        if ks.startswith(
            ("Cognitive.", "Metacognitive.", "Coordinative.", "Socio-emotional."),
        ):
            try:
                raw[k] = min(float(raw[k]), 1.08)
            except (TypeError, ValueError):
                raw[k] = 1.0
    try:
        cur = float(raw.get(new_label, 0.0))
    except (TypeError, ValueError):
        cur = 0.0
    raw[new_label] = max(cur, 4.9)
    for code in allowed_codes:
        if code not in raw:
            raw[code] = 0.0


def extract_primary_label_from_output(out: dict[str, Any]) -> str:
    adj = out.get("adjudicator") or {}
    finals = adj.get("final_labels") or []
    if finals and isinstance(finals[0], dict):
        return str(finals[0].get("label") or "").strip()
    return ""


def build_consistency_retry_instruction(payload: dict[str, Any]) -> str:
    """Text injected into session context for all four agents on retry round."""
    lines = [
        "### Consistency checking — retry round (mandatory)",
        "Consecutive interactive turns were assigned **different events (Tier1)** while the **acts** suggest one communicative sequence "
        "(e.g. ask/answer, give/agree, give/disagree, give/build on). **Events must align** across such pairs; **act (tier2) is the reference** for fixing event when they disagree.",
        "",
        "**Re-read** the code framework (taxonomy + golden-labels) and the **whole session window**.",
        "**Task:** Re-analyze the **current** utterance so its **Tier1.tier2** code is **consistent** with the adjacent turn described below.",
        "",
    ]
    pair = payload.get("pair_summary") or ""
    if pair:
        lines.append(pair)
    res = payload.get("resolution_hint") or ""
    if res:
        lines.append("")
        lines.append(f"**Guidance:** {res}")
    lines.append("")
    lines.append("Output a **revised** full pipeline JSON as usual; prioritize fixing **event** alignment while keeping **act** evidence-based.")
    return "\n".join(lines)


def apply_consistency_checking(
    out: dict[str, Any],
    cleaned_prompt: str,
    context: dict[str, Any] | None,
    allowed_codes: list[str],
) -> None:
    """
    Mutates `out`: sets adjudicator['consistency_checking'] and may repair final label + label_scores.
    """
    ctx = dict(context or {})
    adj = out.setdefault("adjudicator", {})
    allowed_set = set(allowed_codes)
    retry_count = int(ctx.get("consistency_llm_retry_count") or 0)

    prev_l = str(ctx.get("neighbor_previous_predicted_label") or "").strip()
    prev_t = str(ctx.get("neighbor_previous_prompt") or "").strip()
    next_l = str(ctx.get("neighbor_next_predicted_label") or "").strip()
    next_t = str(ctx.get("neighbor_next_prompt") or "").strip()

    curr_l = extract_primary_label_from_output(out)
    if not curr_l:
        adj["consistency_checking"] = {
            "status": "skipped",
            "reason": "No primary label on current turn.",
        }
        return

    # Phase A — same Cognitive tier1: align concept_exploration vs solution_development when 上下文 continues.
    if prev_l:
        if _repair_cognitive_ce_vs_sd_if_needed(
            out, cleaned_prompt, ctx, prev_l, curr_l, allowed_codes, prev_prompt=prev_t
        ):
            return

    # Phase A2 — same Metacognitive tier1: align monitoring vs planning (ask about next step → assent).
    if prev_l:
        if _repair_metacognitive_monitoring_vs_planning_if_needed(
            out, cleaned_prompt, ctx, prev_l, curr_l, allowed_codes, prev_prompt=prev_t
        ):
            return

    curr_l = extract_primary_label_from_output(out)

    # Phase B — cross-tier1 (event) alignment: prefer forward (current vs next) when both exist.
    use_forward = bool(next_l and next_t)
    use_backward = bool(prev_l) and not use_forward

    if not prev_l and not next_l:
        adj["consistency_checking"] = {
            "status": "skipped",
            "reason": "No neighbor predicted label in context (pass neighbor_previous_* / neighbor_next_* after batch or session state).",
        }
        return

    if not use_forward and not use_backward:
        adj["consistency_checking"] = {
            "status": "skipped",
            "reason": "Neighbor labels present but missing prompts for pair check (pass neighbor_*_prompt).",
        }
        return

    if use_forward:
        role = "current_vs_next"
        text_first, text_second = cleaned_prompt, next_t
        label_first, label_second = curr_l, next_l
        neighbor_text_preview = next_t
        neighbor_label_for_display = next_l
    else:
        role = "previous_vs_current"
        text_first, text_second = prev_t, cleaned_prompt
        label_first, label_second = prev_l, curr_l
        neighbor_text_preview = prev_t
        neighbor_label_for_display = prev_l

    def _pv(s: str) -> str:
        s = (s or "").strip()
        return (s[:220] + "…") if len(s) > 220 else s

    pair_kind = _interactive_pair_type(text_first, text_second, label_first, label_second)
    ev_first, _a1 = parse_event_act(label_first)
    ev_second, _a2 = parse_event_act(label_second)
    event_mismatch = bool(ev_first and ev_second and ev_first != ev_second)

    nb_ev, nb_act = parse_event_act(neighbor_label_for_display)

    base: dict[str, Any] = {
        "status": "passed",
        "pair_role": role,
        "interactive_sequence": pair_kind,
        "event_mismatch": event_mismatch,
        "current_sentence_preview": _pv(cleaned_prompt),
        "current_code": {"full": curr_l, "event": parse_event_act(curr_l)[0], "act": parse_event_act(curr_l)[1]},
        "neighbor_sentence_preview": _pv(neighbor_text_preview),
        "neighbor_code": {
            "full": neighbor_label_for_display,
            "event": nb_ev,
            "act": nb_act,
        },
        "resolution": None,
        "action": "none",
        "retry_required": False,
        "retry_instruction_for_agents": "",
    }

    if not event_mismatch:
        base["status"] = "passed"
        base["resolution"] = "Events already match; no change."
        adj["consistency_checking"] = base
        return

    if not pair_kind:
        base["status"] = "failed"
        base["resolution"] = "Tier1 differs between neighbors but no interactive pair heuristic matched."
        base["retry_required"] = retry_count < 1
        if base["retry_required"]:
            base["retry_instruction_for_agents"] = build_consistency_retry_instruction(
                {
                    "pair_summary": _pair_summary_markdown(role, text_first, label_first, text_second, label_second),
                    "resolution_hint": "Determine whether these two turns form one communicative sequence; if yes, align **Tier1** using **Tier2 (act)** as reference.",
                }
            )
        adj["consistency_checking"] = base
        return

    # Longer utterance = anchor (stronger act reference); align shorter turn's event to anchor's event.
    len_a, len_b = len(text_first.strip()), len(text_second.strip())
    if len_a >= len_b:
        anchor_label, short_label = label_first, label_second
        anchor_text = text_first
    else:
        anchor_label, short_label = label_second, label_first
        anchor_text = text_second

    target_event, _ = parse_event_act(anchor_label)
    new_short = _map_label_to_event_preserving_act(short_label, target_event, allowed_set)
    if not new_short:
        new_short = _pick_fallback_for_event(target_event, allowed_codes, pair_kind=pair_kind)

    if not new_short or new_short == short_label:
        base["status"] = "failed"
        base["resolution"] = "Could not auto-map act to a single code under the anchor event."
        base["retry_required"] = retry_count < 1
        if base["retry_required"]:
            base["retry_instruction_for_agents"] = build_consistency_retry_instruction(
                {
                    "pair_summary": _pair_summary_markdown(role, text_first, label_first, text_second, label_second),
                    "resolution_hint": f"Anchor turn (longer) suggests event `{target_event}`; align the shorter turn using act reference; anchor preview: {anchor_text[:120]}",
                }
            )
        adj["consistency_checking"] = base
        return

    # Repair only if the **shorter** turn is this pipeline row (cleaned_prompt).
    if use_forward:
        shorter_is_this_prompt = len(cleaned_prompt.strip()) <= len(next_t.strip())
    else:
        shorter_is_this_prompt = len(cleaned_prompt.strip()) <= len(prev_t.strip())

    if not shorter_is_this_prompt:
        base["status"] = "passed"
        base["resolution"] = (
            f"Event mismatch ({pair_kind}): the **longer** turn is this row; the **neighbor** row’s label should be aligned to `{new_short}` when that row is reprocessed — no change here."
        )
        base["neighbor_suggested_code"] = new_short
        adj["consistency_checking"] = base
        return

    # Mutate current pipeline output (shorter = current prompt)
    finals = adj.get("final_labels")
    note = ""
    if isinstance(finals, list) and finals and isinstance(finals[0], dict):
        old = finals[0].get("label")
        finals[0]["label"] = new_short
        finals[0]["decision"] = "combine_both"
        note = (
            f"Consistency checking: aligned **event** to match interactive pair `{pair_kind}` "
            f"(act as reference); adjusted `{old}` → `{new_short}`."
        )
        finals[0]["rationale"] = note + "\n\n" + str(finals[0].get("rationale") or "")

    lc = out.get("label_coder") or {}
    labs = lc.get("labels")
    if isinstance(labs, list) and labs and isinstance(labs[0], dict):
        labs[0]["label"] = new_short
        labs[0]["rationale"] = (labs[0].get("rationale") or "") + (" | " + note if note else "")
    _bump_scores_for_label(lc, new_short, allowed_codes)

    base["status"] = "repaired"
    base["action"] = "align_shorter_turn_event_to_anchor"
    base["resolution"] = f"Repaired current label to `{new_short}` (sequence: {pair_kind})."
    base["repaired_label"] = new_short
    adj["consistency_checking"] = base


def _pair_summary_markdown(
    role: str,
    t_a: str,
    l_a: str,
    t_b: str,
    l_b: str,
) -> str:
    return (
        f"- **Pair:** `{role}`\n"
        f"- **Turn A:** {t_a[:300]}{'…' if len(t_a) > 300 else ''}\n"
        f"  - Code: `{l_a}`\n"
        f"- **Turn B:** {t_b[:300]}{'…' if len(t_b) > 300 else ''}\n"
        f"  - Code: `{l_b}`"
    )


def append_consistency_to_adjudicator_rationale(out: dict[str, Any]) -> None:
    """After _finalize_adjudicator_with_boundary_critic, append consistency block to rationale."""
    adj = out.get("adjudicator") or {}
    cc = adj.get("consistency_checking")
    if not isinstance(cc, dict) or cc.get("status") == "skipped":
        return
    block = format_consistency_markdown_block(cc)
    finals = adj.get("final_labels")
    if not isinstance(finals, list) or not finals or not isinstance(finals[0], dict):
        return
    f0 = finals[0]
    prev = (f0.get("rationale") or "").strip()
    if "**Step 1 — Code framework:**" in prev:
        return
    f0["rationale"] = (prev + "\n\n" + block).strip()
    adj["adjudication_analysis"] = (str(adj.get("adjudication_analysis") or "").strip() + "\n\n" + block).strip()


def format_consistency_markdown_block(cc: dict[str, Any]) -> str:
    """Structured markdown for Discord / logs."""
    cur = cc.get("current_code") or {}
    nb = cc.get("neighbor_code") or {}
    lines = [
        "**Step 1 — Code framework:** Use taxonomy + golden-labels (event = Tier1, act = Tier2).",
        "**Step 2 — Current:** review `current_code` vs utterance.",
        "**Step 3 — Neighbor:** compare with adjacent turn in the same discussion.",
        "**Step 4 — Alignment:** if events differ but acts form an interactive pair, align **event** using **act** as reference. "
        "Within **Cognitive**, keep **concept_exploration** vs **solution_development** stable; within **Metacognitive**, "
        "keep **monitoring** vs **planning** stable when the reply assents to a progress/next-step question.",
        "",
        "```",
        f"{'':22}  Event (Tier1)   Act (Tier2)   Full code",
        f"{'Current turn':22}  {str(cur.get('event', '—')):14} {str(cur.get('act', '—')):12} {cur.get('full', '—')}",
        f"{'Neighbor turn':22}  {str(nb.get('event', '—')):14} {str(nb.get('act', '—')):12} {nb.get('full', '—')}",
        "```",
    ]
    lines.extend(
        [
            "",
            f"- **Status:** `{cc.get('status', '')}`",
            f"- **Pair role:** `{cc.get('pair_role', '—')}`",
            f"- **Interactive sequence:** `{cc.get('interactive_sequence') or '—'}`",
            f"- **Event mismatch:** {'yes' if cc.get('event_mismatch') else 'no'}",
        ]
    )
    pv = cc.get("current_sentence_preview")
    nv = cc.get("neighbor_sentence_preview")
    if pv:
        lines.append(f"- **Current preview:** {pv}")
    if nv:
        lines.append(f"- **Neighbor preview:** {nv}")
    if cc.get("resolution"):
        lines.append(f"- **Resolution:** {cc['resolution']}")
    if cc.get("repaired_label"):
        lines.append(f"- **Repaired label:** `{cc['repaired_label']}`")
    if cc.get("retry_required"):
        lines.append("- **LLM retry:** requested — see **Context → Consistency retry** on all four role messages.")
    return "\n".join(lines)
