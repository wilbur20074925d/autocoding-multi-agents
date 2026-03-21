"""
Discord response dispatcher: 1 orchestrator + 4 display bots.

Takes structured output from the OpenClaw autocoding pipeline and produces
four Discord-ready messages, one per agent role, so the main controller can
post them sequentially using four different Discord bot identities.

Backend (agents, skills, taxonomy, workflows) is unchanged and runs inside the
OpenClaw runtime. This module is the only new layer: it maps pipeline output
to per-bot messages.
"""

from __future__ import annotations

from typing import Any

from .format import (
    DISCORD_MAX_LEN,
    bullet_list,
    section,
    split_messages,
    table_from_rows,
    key_value_pairs,
    truncate,
    format_final_answer_summary,
    format_evidence_spans_full,
    format_full_prompt_section,
    format_boundary_challenge_block,
    fenced_plain_text,
)
from .pipeline_output import PipelineOutput

# Role ids for the 4 display bots (order matches pipeline)
SIGNAL_EXTRACTOR = "signal_extractor"
LABEL_CODER = "label_coder"
BOUNDARY_CRITIC = "boundary_critic"
ADJUDICATOR = "adjudicator"

DISPLAY_BOT_ORDER = [SIGNAL_EXTRACTOR, LABEL_CODER, BOUNDARY_CRITIC, ADJUDICATOR]


def _format_context(context: dict[str, Any] | None) -> str | None:
    """
    Format shared metadata (group/timestamp/people/context) so *all four roles*
    can condition their reasoning on multi-group discussion context.
    """
    if not context:
        return None
    pairs: list[tuple[str, Any]] = []
    for k in ("group", "timestamp", "people", "context"):
        v = context.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            pairs.append((k, s))
    if not pairs:
        return None
    return key_value_pairs(pairs, title="Context")


def _parse_label_struct(label: Any) -> tuple[str, str, str]:
    """
    Normalize a label into (main_label, sublabel, subsublabel).

    Supports:
    - legacy dotted codes: "Tier1.tier2.tier3" (or "Tier1.tier2")
    - dict labels: {"main_label": ..., "sublabel": ..., "subsublabel": ...}
    - loose separators: "Tier1 | tier2 | tier3" or "Tier1 > tier2 > tier3"
    """
    if isinstance(label, dict):
        main = str(label.get("main_label") or label.get("tier1") or "").strip()
        sub = str(label.get("sublabel") or label.get("tier2") or "").strip()
        subsub = str(label.get("subsublabel") or label.get("tier3") or "").strip()
        return main, sub, subsub

    s = str(label or "").strip()
    if not s:
        return "", "", ""

    # Try dotted code first.
    if "." in s:
        parts = [p.strip() for p in s.split(".") if p.strip()]
        main = parts[0] if len(parts) >= 1 else ""
        sub = parts[1] if len(parts) >= 2 else ""
        subsub = parts[2] if len(parts) >= 3 else ""
        return main, sub, subsub

    # Try common human separators.
    for sep in ("|", ">", "→", "/"):
        if sep in s:
            parts = [p.strip() for p in s.split(sep) if p.strip()]
            main = parts[0] if len(parts) >= 1 else ""
            sub = parts[1] if len(parts) >= 2 else ""
            subsub = parts[2] if len(parts) >= 3 else ""
            return main, sub, subsub

    return s, "", ""


def _label_struct_row(label: Any) -> list[str]:
    main, sub, subsub = _parse_label_struct(label)
    # Display-friendly main label (align with your "X interactions" scheme) while
    # keeping legacy compatibility for matching/eval.
    main_disp = f"{main} interactions" if main and "interaction" not in main.lower() else main
    return [main_disp, sub, subsub]


def _format_signal_extractor(data: dict[str, Any] | None) -> str:
    """Format Signal Extractor output for Discord."""
    if not data:
        return section("Signal Extractor", "_No output_")
    parts = []
    evidence = data.get("evidence_spans") or []
    if evidence:
        parts.append(format_evidence_spans_full(evidence))
    else:
        parts.append(section("Evidence spans", "_None_"))
    candidates = data.get("candidate_signals") or []
    if candidates:
        items = []
        for c in candidates[:15]:
            if isinstance(c, dict):
                items.append(str(c.get("candidates", c))[:100])
            else:
                items.append(str(c)[:100])
        parts.append(bullet_list(items, header="Candidate signals"))
    ambiguity = data.get("ambiguity") or []
    if ambiguity:
        items = [str(a)[:120] for a in ambiguity[:10]]
        parts.append(bullet_list(items, header="Ambiguity"))
    return "\n\n".join(parts) if parts else section("Signal Extractor", "_No output_")


def _format_label_coder(data: dict[str, Any] | None) -> str:
    """Format Label Coder output for Discord."""
    if not data:
        return section("Label Coder", "_No output_")
    parts = []
    labels = data.get("labels") or []
    if labels:
        rows = []
        for i, item in enumerate(labels):
            if isinstance(item, dict):
                rows.append([
                    *_label_struct_row(item.get("label", "")),
                    (item.get("evidence_used") or "")[:50],
                    (item.get("rationale") or "")[:60],
                ])
            else:
                rows.append([*_label_struct_row(item), "", "", ""])
        parts.append(table_from_rows(["Main label", "Sublabel", "Subsublabel", "Evidence", "Rationale"], rows, title="Labels"))
    else:
        parts.append(section("Labels", "_None_"))
    disp = data.get("label_scores_display")
    if disp:
        parts.append(section("Semantic fit table (0–5 per label)", truncate(str(disp), max_len=1600)))
    if data.get("scores_close"):
        parts.append(
            section(
                "Scores close?",
                f"**Yes** — margin #1−#2 = `{data.get('label_scores_margin_top2')}`. Boundary Critic should refine.",
            )
        )
    uncertain = data.get("uncertain") or []
    if uncertain:
        parts.append(bullet_list([str(u)[:100] for u in uncertain], header="Uncertain"))
    revision_note = data.get("revision_note")
    if revision_note:
        parts.append(section("Revision note", truncate(str(revision_note), max_len=400)))
    return "\n\n".join(parts) if parts else section("Label Coder", "_No output_")


def _format_boundary_critic(data: dict[str, Any] | None) -> str:
    """Format Boundary Critic output for Discord (full interaction: index + structured reasoning, no truncation)."""
    if not data:
        return section("🛡 Boundary Critic", "_No output_")
    parts: list[str] = []
    challenges = data.get("challenges") or []
    requests = data.get("request_missing_evidence") or []
    parts.append(
        section(
            "Boundary Review Summary",
            f"Challenges: **{len(challenges)}** | Missing evidence requests: **{len(requests)}**",
        )
    )
    if challenges:
        rows: list[list[Any]] = []
        for i, c in enumerate(challenges[:24], start=1):
            if isinstance(c, dict):
                must = "Y" if c.get("must_challenge") else ""
                rows.append([
                    i,
                    str(c.get("assigned_label", c.get("span_ref", ""))),
                    str(c.get("suggested_alternative") or "—"),
                    str(c.get("margin")) if c.get("margin") is not None else "—",
                    must,
                ])
            else:
                rows.append([i, str(c), "—", "—", ""])
        parts.append(
            table_from_rows(
                ["#", "Assigned label", "Alternative", "Margin", "Must"],
                rows,
                title="Challenges (index)",
            )
        )
        detail_blocks: list[str] = []
        for i, c in enumerate(challenges[:24], start=1):
            if isinstance(c, dict):
                detail_blocks.append(format_boundary_challenge_block(c, i))
            else:
                detail_blocks.append(f"**━━ Challenge {i}** (non-structured)\n{fenced_plain_text(str(c))}")
        parts.append("**Structured reasoning (full)**\n\n" + "\n\n".join(detail_blocks))
    else:
        parts.append(section("Challenges", "_None_"))
    if requests:
        rows = []
        for r in requests[:12]:
            if isinstance(r, dict):
                rows.append([
                    str(r.get("part_of_prompt") or ""),
                    str(r.get("reason") or ""),
                ])
            else:
                rows.append(["", str(r)])
        parts.append(table_from_rows(["Missing part", "Reason"], rows, title="Request missing evidence"))
    body = "\n\n".join(parts) if parts else "_No output_"
    return section("🛡 Boundary Critic", body)


def _split_labels(cell: str) -> set[str]:
    raw = (cell or "").strip()
    if not raw:
        return set()
    # Accept comma/semicolon separated labels, ignore empties.
    parts = [p.strip() for p in raw.replace(";", ",").split(",")]
    return {p for p in parts if p}


def _predicted_labels(final_labels: list[Any]) -> set[str]:
    labels: set[str] = set()
    for item in final_labels or []:
        if isinstance(item, dict):
            v = item.get("label")
            if v:
                labels.add(str(v).strip())
        else:
            s = str(item).strip()
            if s:
                labels.add(s)
    return labels


def _format_hc_check(context: dict[str, Any] | None, final_labels: list[Any]) -> str | None:
    if not context:
        return None
    hc1 = str(context.get("HC1", "") or "").strip()
    hc2 = str(context.get("HC2", "") or "").strip()
    if not hc1 and not hc2:
        return None

    # HC check is now posted by the Controller as a separate message.
    # Keep this helper for potential non-Discord consumers, but do not render verdict here.
    return section(
        "HC check",
        "\n".join(
            [
                f"**HC1:** {hc1 or '_empty_'}",
                f"**HC2:** {hc2 or '_empty_'}",
            ]
        ),
    )


def _format_adjudicator(data: dict[str, Any] | None, context: dict[str, Any] | None = None) -> str:
    """Format Adjudicator output for Discord (final answer summary + table + uncertain/retry)."""
    if not data:
        return section("⚖ Adjudicator", "_No output_")
    parts = []
    final_labels = data.get("final_labels") or []
    # Prominent one-line summary at top
    parts.append(format_final_answer_summary(final_labels))
    if final_labels:
        rows = []
        for i, item in enumerate(final_labels):
            if isinstance(item, dict):
                rows.append([
                    *_label_struct_row(item.get("label", "")),
                    item.get("decision", ""),
                    (item.get("rationale") or "")[:70],
                ])
            else:
                rows.append([*_label_struct_row(item), "", ""])
        parts.append(table_from_rows(["Main label", "Sublabel", "Subsublabel", "Decision", "Rationale"], rows, title="Details"))
    else:
        parts.append(section("Details", "_None_"))
    uncertain = data.get("uncertain") or []
    if uncertain:
        parts.append(bullet_list([str(u)[:100] for u in uncertain], header="Uncertain"))
    retry = data.get("retry")
    if retry and isinstance(retry, dict):
        parts.append(
            key_value_pairs(
                [
                    ("Retry target", retry.get("target", "")),
                    ("Instruction", truncate(str(retry.get("instruction", "")), max_len=200)),
                ],
                title="Retry",
            )
        )
    # HC check is posted by the Controller (separate message).
    body = "\n\n".join(parts) if parts else "_No output_"
    return section("⚖ Adjudicator", body)


def prepare_four_bot_messages(
    pipeline_output: PipelineOutput | dict[str, Any],
    *,
    include_prompt_in_first: bool = True,
    max_message_len: int = DISCORD_MAX_LEN,
) -> list[tuple[str, str]]:
    """
    Build four Discord messages from pipeline output, one per display bot.

    Args:
        pipeline_output: Full result from OpenClaw (keys: signal_extractor, label_coder,
                         boundary_critic, adjudicator; optional: prompt, context).
        include_prompt_in_first: If True, prepend the **full** prompt (code block / quoted) to the Signal Extractor message.
        max_message_len: Truncate each bot message to this length (default Discord-safe).

    Returns:
        List of (role_id, message) in order: signal_extractor, label_coder, boundary_critic, adjudicator.
        Message may be truncated to max_message_len. Use discord.format.split_messages in the
        controller if you need to post multiple chunks per bot.
    """
    prompt = (pipeline_output.get("prompt") or "") if include_prompt_in_first else ""
    ctx = pipeline_output.get("context") if isinstance(pipeline_output, dict) else None
    ctx_block = _format_context(ctx)
    out: list[tuple[str, str]] = []

    # 1. Signal Extractor
    se_data = pipeline_output.get("signal_extractor")
    msg1 = _format_signal_extractor(se_data)
    if prompt:
        msg1 = format_full_prompt_section(prompt) + "\n\n" + msg1
    if ctx_block:
        msg1 = ctx_block + "\n\n" + msg1
    out.append((SIGNAL_EXTRACTOR, truncate(msg1, max_len=max_message_len)))

    # 2. Label Coder
    lc_data = pipeline_output.get("label_coder")
    msg2 = _format_label_coder(lc_data)
    if ctx_block:
        msg2 = ctx_block + "\n\n" + msg2
    out.append((LABEL_CODER, truncate(msg2, max_len=max_message_len)))

    # 3. Boundary Critic
    bc_data = pipeline_output.get("boundary_critic")
    msg3 = _format_boundary_critic(bc_data)
    if ctx_block:
        msg3 = ctx_block + "\n\n" + msg3
    out.append((BOUNDARY_CRITIC, truncate(msg3, max_len=max_message_len)))

    # 4. Adjudicator
    adj_data = pipeline_output.get("adjudicator")
    msg4 = _format_adjudicator(adj_data, ctx)
    if ctx_block:
        msg4 = ctx_block + "\n\n" + msg4
    out.append((ADJUDICATOR, truncate(msg4, max_len=max_message_len)))

    return out


def prepare_four_bot_messages_split(
    pipeline_output: PipelineOutput | dict[str, Any],
    *,
    include_prompt_in_first: bool = True,
    max_chunk_len: int = DISCORD_MAX_LEN,
) -> list[tuple[str, list[str]]]:
    """
    Same as prepare_four_bot_messages but each bot gets a list of message chunks
    (for posting multiple messages per bot when content is long).
    """
    # Build without per-message truncation; split_messages applies Discord-safe chunking.
    raw = prepare_four_bot_messages(
        pipeline_output,
        include_prompt_in_first=include_prompt_in_first,
        max_message_len=1_000_000,
    )
    result: list[tuple[str, list[str]]] = []
    for role_id, text in raw:
        chunks = split_messages(text, max_len=max_chunk_len)
        result.append((role_id, chunks if chunks else ["_No content_"]))
    return result
