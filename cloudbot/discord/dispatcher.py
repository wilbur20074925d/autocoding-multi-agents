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
)
from .pipeline_output import PipelineOutput

# Role ids for the 4 display bots (order matches pipeline)
SIGNAL_EXTRACTOR = "signal_extractor"
LABEL_CODER = "label_coder"
BOUNDARY_CRITIC = "boundary_critic"
ADJUDICATOR = "adjudicator"

DISPLAY_BOT_ORDER = [SIGNAL_EXTRACTOR, LABEL_CODER, BOUNDARY_CRITIC, ADJUDICATOR]


def _format_signal_extractor(data: dict[str, Any] | None) -> str:
    """Format Signal Extractor output for Discord."""
    if not data:
        return section("Signal Extractor", "_No output_")
    parts = []
    evidence = data.get("evidence_spans") or []
    if evidence:
        rows = []
        for i, span in enumerate(evidence[:20]):
            if isinstance(span, dict):
                rows.append([
                    span.get("span", str(span))[:60],
                    span.get("start", ""),
                    span.get("end", ""),
                ])
            else:
                rows.append([str(span)[:60], "", ""])
        parts.append(table_from_rows(["Span", "Start", "End"], rows, title="Evidence spans"))
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
                    item.get("label", item.get("span_ref", i)),
                    (item.get("evidence_used") or "")[:50],
                    (item.get("rationale") or "")[:60],
                ])
            else:
                rows.append([str(item), "", ""])
        parts.append(table_from_rows(["Label", "Evidence", "Rationale"], rows, title="Labels"))
    else:
        parts.append(section("Labels", "_None_"))
    uncertain = data.get("uncertain") or []
    if uncertain:
        parts.append(bullet_list([str(u)[:100] for u in uncertain], header="Uncertain"))
    revision_note = data.get("revision_note")
    if revision_note:
        parts.append(section("Revision note", truncate(str(revision_note), max_len=400)))
    return "\n\n".join(parts) if parts else section("Label Coder", "_No output_")


def _format_boundary_critic(data: dict[str, Any] | None) -> str:
    """Format Boundary Critic output for Discord."""
    if not data:
        return section("Boundary Critic", "_No output_")
    parts = []
    challenges = data.get("challenges") or []
    if challenges:
        rows = []
        for c in challenges[:15]:
            if isinstance(c, dict):
                rows.append([
                    str(c.get("assigned_label", c.get("span_ref", "")))[:40],
                    (c.get("question") or "")[:50],
                    (c.get("reason") or "")[:60],
                ])
            else:
                rows.append([str(c)[:40], "", ""])
        parts.append(table_from_rows(["Label / span", "Question", "Reason"], rows, title="Challenges"))
    else:
        parts.append(section("Challenges", "_None_"))
    requests = data.get("request_missing_evidence") or []
    if requests:
        items = []
        for r in requests[:10]:
            if isinstance(r, dict):
                items.append((r.get("reason") or str(r))[:100])
            else:
                items.append(str(r)[:100])
        parts.append(bullet_list(items, header="Request missing evidence"))
    return "\n\n".join(parts) if parts else section("Boundary Critic", "_No output_")


def _format_adjudicator(data: dict[str, Any] | None) -> str:
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
                    item.get("label", item.get("span_ref", i)),
                    item.get("decision", ""),
                    (item.get("rationale") or "")[:70],
                ])
            else:
                rows.append([str(item), "", ""])
        parts.append(table_from_rows(["Label", "Decision", "Rationale"], rows, title="Details"))
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
        include_prompt_in_first: If True, prepend a short prompt summary to the Signal Extractor message.
        max_message_len: Truncate each bot message to this length (default Discord-safe).

    Returns:
        List of (role_id, message) in order: signal_extractor, label_coder, boundary_critic, adjudicator.
        Message may be truncated to max_message_len. Use discord.format.split_messages in the
        controller if you need to post multiple chunks per bot.
    """
    prompt = (pipeline_output.get("prompt") or "") if include_prompt_in_first else ""
    out: list[tuple[str, str]] = []

    # 1. Signal Extractor
    se_data = pipeline_output.get("signal_extractor")
    msg1 = _format_signal_extractor(se_data)
    if prompt:
        msg1 = section("Prompt", truncate(prompt, max_len=400)) + "\n\n" + msg1
    out.append((SIGNAL_EXTRACTOR, truncate(msg1, max_len=max_message_len)))

    # 2. Label Coder
    lc_data = pipeline_output.get("label_coder")
    out.append((LABEL_CODER, truncate(_format_label_coder(lc_data), max_len=max_message_len)))

    # 3. Boundary Critic
    bc_data = pipeline_output.get("boundary_critic")
    out.append((BOUNDARY_CRITIC, truncate(_format_boundary_critic(bc_data), max_len=max_message_len)))

    # 4. Adjudicator
    adj_data = pipeline_output.get("adjudicator")
    out.append((ADJUDICATOR, truncate(_format_adjudicator(adj_data), max_len=max_message_len)))

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
    raw = prepare_four_bot_messages(
        pipeline_output,
        include_prompt_in_first=include_prompt_in_first,
        max_message_len=max_chunk_len * 10,
    )
    result: list[tuple[str, list[str]]] = []
    for role_id, text in raw:
        chunks = split_messages(text, max_len=max_chunk_len)
        result.append((role_id, chunks if chunks else ["_No content_"]))
    return result
