"""
Format autocoding pipeline output for Discord.

Discord supports: **bold**, __underline__, `inline code`, ```code blocks```,
bullet lists (•), blockquote (>). Markdown tables are not rendered; use
code blocks with monospace alignment. Max message length is 2000 characters.
"""

from __future__ import annotations

import json
from typing import Any

# Discord message length limit (leave margin for embeds/formatting)
DISCORD_MAX_LEN = 1900


def truncate(text: str, max_len: int = DISCORD_MAX_LEN, suffix: str = "...") -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - len(suffix)] + suffix


def fenced_plain_text(text: str) -> str:
    """
    Wrap text in a Discord ``` fence when safe; otherwise use blockquote lines.
    Does not truncate — use split_messages() on the parent message for Discord limits.
    """
    raw = (text or "").replace("\r\n", "\n")
    if not raw:
        return "_empty_"
    if "```" in raw:
        # Avoid breaking the fence: quote each line (readable for long prompts).
        return "\n".join(f"> {line}" if line else ">" for line in raw.split("\n"))
    return f"```\n{raw}\n```"


def format_full_prompt_section(prompt: str | None) -> str:
    """Full prompt for Signal Extractor (and similar); no 400-char cap."""
    p = (prompt or "").strip()
    if not p:
        return section("Prompt", "_empty_")
    return section("Prompt", fenced_plain_text(p))


def format_session_overview_discord(overview: dict[str, Any] | None) -> str:
    """
    Session window block: same-group neighbors + heuristic cognitive focus.
    Shown **between** Prompt and Evidence spans on the Signal Extractor message.
    """
    if not overview:
        return section(
            "📎 Session window (same group)",
            "_No session overview._",
        )
    if not overview.get("has_neighbors"):
        return section(
            "📎 Session window (same group)",
            str(
                overview.get("summary_line")
                or "_No neighboring prompts — pass `session_prompts_before` / `session_prompts_after` in context (e.g. CSV batch) or use group+time-ordered rows._",
            ),
        )

    tilt = str(overview.get("cognitive_tilt") or "neutral")
    nb = overview.get("neighbor_counts") or {}
    sf = str(overview.get("semantic_focus") or "")
    sf_hint = ""
    if sf == "learning_task_solutions":
        sf_hint = "**Whole-session focus:** **solutions for the learning task** (answers, options, classifying the response) — prefer **Cognitive.solution_development** when the current line is ambiguous."
    elif sf == "learning_task_concepts":
        sf_hint = "**Whole-session focus:** **concepts of the learning task** (meanings, theory) — prefer **Cognitive.concept_exploration** when the current line is ambiguous."
    lines: list[str] = [
        f"**Cognitive activity focus (heuristic):** `{tilt}`",
    ]
    if sf_hint:
        lines.extend(["", sf_hint])
    lines.extend(
        [
            "",
            str(overview.get("summary_line") or ""),
            "",
            f"**Utterances in window:** ⬆️ before **{nb.get('before', 0)}** · 📍 current **1** · ⬇️ after **{nb.get('after', 0)}**",
        ]
    )
    th = overview.get("task_cue_hits")
    dh = overview.get("def_cue_hits")
    if th is not None and dh is not None:
        lines.append(f"**Cue counts (task-solution vs definition-style):** {th} vs {dh}")

    agg = overview.get("aggregate_preview") or {}
    if agg:
        top_fmt = " · ".join(f"`{k}` {v}" for k, v in list(agg.items())[:5])
        lines.extend(["", f"**Session-averaged fit (top codes):** {top_fmt}"])

    lines.extend(
        [
            "",
            "**Per-utterance top label** (semantic proxy, orientation only — **not** final taxonomy decision)",
            "_Scores are calibrated on 0–5. Use the **whole window** for CE vs SD: **concepts *of* the task** vs **solutions *for* the task**; per-line tops are orientation only._",
        ]
    )
    for row in overview.get("per_utterance_top") or []:
        role = str(row.get("role") or "")
        pv = str(row.get("preview") or "")
        lab = str(row.get("top_label") or "")
        sc = row.get("top_score", "")
        role_disp = {"before": "⬆️ Earlier", "after": "⬇️ Later", "current": "📍 Current"}.get(role, role)
        lines.append(f"• **{role_disp}** → `{lab}` ({sc}) — {pv}")

    return section("📎 Session window (same group · time order)", "\n".join(lines))


def format_evidence_spans_full(
    evidence: list[Any],
    *,
    max_spans: int = 64,
    include_overview_table: bool = True,
) -> str:
    """
    Evidence spans with **verbatim full text** per span (no column truncation).
    Optional compact overview table (#, start, end, len) for scanning.
    """
    if not evidence:
        return section("Evidence spans", "_None_")
    parts: list[str] = []
    rows: list[list[Any]] = []
    blocks: list[str] = []
    for i, span in enumerate(evidence[:max_spans]):
        if isinstance(span, dict):
            s = span.get("span", "")
            st = span.get("start", "")
            en = span.get("end", "")
            reason = (span.get("reason") or "").strip()
            slen = len(s) if isinstance(s, str) else 0
            ss = s if isinstance(s, str) else str(s)
            rows.append([i, st, en, slen])
            hdr = f"**Span [{i}]** · `{st}`–`{en}` · {slen} chars"
            if reason:
                hdr += f"\n_{reason}_"
            blocks.append(hdr + "\n" + fenced_plain_text(ss))
        else:
            rows.append([i, "", "", len(str(span))])
            blocks.append(f"**Span [{i}]**\n{fenced_plain_text(str(span))}")
    if include_overview_table and rows:
        parts.append(
            table_from_rows(["#", "Start", "End", "Len"], rows, title="Evidence spans — overview"),
        )
    parts.append("**Evidence spans — full text**\n\n" + "\n\n".join(blocks))
    if len(evidence) > max_spans:
        parts.append(f"\n_…and {len(evidence) - max_spans} more span(s) not shown (raise max_spans)._")
    return "\n\n".join(parts)


def section(title: str, body: str, *, use_blockquote: bool = False) -> str:
    """Format a section with bold title and body. Optionally wrap body in blockquote."""
    if use_blockquote:
        return f"**{title}**\n> {body.replace(chr(10), chr(10) + '> ')}"
    return f"**{title}**\n{body}"


def table_from_rows(
    headers: list[str],
    rows: list[list[Any]],
    *,
    title: str | None = None,
) -> str:
    """
    Format a table for Discord using a code block so columns align in monospace.
    """
    def to_cell(x: Any) -> str:
        return str(x).replace("|", "∣")  # avoid breaking table

    pad = len(headers)
    cells = [headers] + [[to_cell(row[i]) if i < len(row) else "" for i in range(pad)] for row in rows]
    widths = [max(len(cells[r][i]) for r in range(len(cells))) for i in range(pad)]
    lines = []
    for i, row in enumerate(cells):
        line = " | ".join(str(c).ljust(widths[j]) for j, c in enumerate(row))
        lines.append(line)
        if i == 0 and len(cells) > 1:
            lines.append("-+-".join("-" * w for w in widths))

    block = "\n".join(lines)
    out = f"```\n{block}\n```"
    if title:
        out = f"**{title}**\n{out}"
    return out


def table_from_dicts(
    rows: list[dict[str, Any]],
    *,
    keys: list[str] | None = None,
    title: str | None = None,
) -> str:
    """Format a list of dicts as a table. Keys = column order if provided."""
    if not rows:
        return section("Table", "_No rows_") if title else "_No rows_"
    keys = keys or list(rows[0].keys())
    headers = keys
    data = [[row.get(k, "") for k in keys] for row in rows]
    return table_from_rows(headers, data, title=title)


def code_block(content: str, language: str = "") -> str:
    """Wrap content in a Discord code block. Content is truncated if too long."""
    content = truncate(content, max_len=DISCORD_MAX_LEN - 10)
    return f"```{language}\n{content}\n```"


def json_block(obj: Any) -> str:
    """Format a JSON-serializable object in a code block."""
    raw = json.dumps(obj, indent=2, ensure_ascii=False)
    return code_block(truncate(raw, max_len=DISCORD_MAX_LEN - 10), "json")


def build_label_scores_display(
    label_scores: dict[str, float],
    *,
    title: str = "Semantic fit scores (0.00–5.00 max per label)",
    max_rows: int | None = None,
) -> str:
    """
    Semi-structured score table for Discord: rank, label, numeric score, top-N marker.
    Use monospace table (code block) for alignment.
    """
    if not label_scores:
        return section(title, "_No scores_")
    ranked = sorted(label_scores.items(), key=lambda kv: (-kv[1], kv[0]))
    if max_rows is not None:
        ranked = ranked[:max_rows]
    rows: list[list[Any]] = []
    for i, (lab, sc) in enumerate(ranked, start=1):
        semi = "★ top" if i == 1 else ("★★" if i == 2 else ("★★★" if i == 3 else ""))
        rows.append([i, lab, f"{float(sc):.2f}", semi])
    return table_from_rows(["#", "Label (Tier1.tier2)", "Score", "Semi"], rows, title=title)


def bullet_list(items: list[str], *, header: str | None = None) -> str:
    """Format a bullet list. Use • for a clean look."""
    lines = [f"• {item}" for item in items]
    body = "\n".join(lines)
    if header:
        return section(header, body)
    return body


def format_boundary_challenge_block(c: dict[str, Any], index: int) -> str:
    """
    One Boundary Critic challenge with full Q / reason / pro / con / reverse test (no mid-field truncation).
    """
    lbl = str(c.get("assigned_label") or c.get("span_ref") or "").strip() or "—"
    q = (c.get("question") or "").strip()
    r = (c.get("reason") or "").strip()
    alt = (c.get("suggested_alternative") or "").strip() or "—"
    pro = (c.get("support_evidence") or "").strip()
    con = (c.get("refute_evidence") or "").strip()
    test = (c.get("counterexample_test") or "").strip()
    margin = c.get("margin")
    must = c.get("must_challenge")
    lines = [
        f"**━━ Challenge {index} · `{lbl}`**",
        f"**Question:** {q}" if q else "**Question:** _—_",
        f"**Reason:** {r}" if r else "**Reason:** _—_",
        f"**Suggested alternative:** `{alt}`",
    ]
    if margin is not None:
        lines.append(f"**Score margin (top1−top2):** `{margin}`")
    if must is not None:
        lines.append(f"**Must challenge:** `{'yes' if must else 'no'}`")
    if pro:
        lines.append(f"**Pro (support):** {pro}")
    if con:
        lines.append(f"**Con (against):** {con}")
    if test:
        lines.append(f"**Reverse / contrast test:** {test}")
    return "\n".join(lines)


def key_value_pairs(
    pairs: list[tuple[str, Any]],
    *,
    title: str | None = None,
) -> str:
    """Format key-value pairs (e.g. Prompt: ..., Final label: ...)."""
    lines = [f"**{k}:** {v}" for k, v in pairs]
    body = "\n".join(lines)
    if title:
        return section(title, body)
    return body


def format_controller_label_ack(
    prompt: str,
    *,
    context: dict[str, Any] | None = None,
    csv_row_index: int | None = None,
    csv_row_total: int | None = None,
    max_prompt_len: int = 900,
    max_neighbor_lines: int = 8,
    neighbor_preview_chars: int = 220,
) -> str:
    """
    Controller “received” message: **metadata**, **session window**, **orchestration**, **prompt** — separate sections.

    `context` should match pipeline input: `group`, `timestamp`, `people`, `context` (scenario tag),
    optional `HC1`/`HC2`, and `session_prompts_before` / `session_prompts_after`.
    """
    ctx = context or {}
    p = (prompt or "").strip()

    def _field(key: str) -> str:
        v = ctx.get(key)
        if v is None:
            return "—"
        s = str(v).strip()
        return s if s else "—"

    group_v = _field("group")
    ts_v = _field("timestamp")
    people_v = _field("people")
    scen_v = _field("context")

    meta_table = table_from_rows(
        ["Field", "Value"],
        [
            ["Group", group_v],
            ["Timestamp", ts_v],
            ["People", people_v],
            ["Context (tag)", scen_v],
        ],
        title="Session metadata",
    )

    hc1, hc2 = _field("HC1"), _field("HC2")
    hc_block = ""
    if hc1 != "—" or hc2 != "—":
        hc_block = "\n\n" + table_from_rows(
            ["Reference", "Value"],
            [["HC1 (golden)", hc1], ["HC2 (golden)", hc2]],
            title="Human-coded reference",
        )

    before = ctx.get("session_prompts_before") or ctx.get("prompts_before") or []
    after = ctx.get("session_prompts_after") or ctx.get("prompts_after") or []
    if not isinstance(before, list):
        before = []
    if not isinstance(after, list):
        after = []

    def _neighbor_lines(items: list[Any]) -> list[str]:
        out: list[str] = []
        shown = 0
        for item in items[:max_neighbor_lines]:
            t = (str(item) if item is not None else "").strip()
            if not t:
                continue
            shown += 1
            one = " ".join(t.split())
            prev = truncate(one, max_len=neighbor_preview_chars)
            out.append(f"**{shown}.** {prev}")
        rest = len(items) - max_neighbor_lines
        if rest > 0:
            out.append(f"_…and {rest} more not listed._")
        return out

    b_lines = _neighbor_lines(before)
    a_lines = _neighbor_lines(after)
    win_parts = [
        f"**Same-group window · before this utterance:** **{len(before)}** line(s) · **after:** **{len(after)}** line(s)",
        "",
    ]
    if b_lines:
        win_parts.append("**Earlier in session (oldest → newest, previews)**")
        win_parts.extend(b_lines)
    else:
        win_parts.append("**Earlier in session:** _none in window_")
    win_parts.append("")
    if a_lines:
        win_parts.append("**Later in file / batch (previews)**")
        win_parts.extend(a_lines)
    else:
        win_parts.append("**Later in file / batch:** _none_ _(normal for live Discord)_")

    session_window = section("Session window (same group · time order)", "\n".join(win_parts))

    orch = section(
        "Orchestration",
        "\n".join(
            [
                "1. **Signal Extractor** — evidence spans + candidate signals (+ session overview)",
                "2. **Label Coder** — label(s) + rationale",
                "3. **Boundary Critic** — boundary challenges / missing evidence",
                "4. **Adjudicator** — final decision",
            ]
        ),
    )

    prompt_body = fenced_plain_text(truncate(p, max_len=max_prompt_len)) if p else "_empty_"
    prompt_sec = section("Prompt to label", prompt_body)

    head = "**CONTROLLER** · Label request received"
    if csv_row_index is not None and csv_row_total is not None:
        head += f"\n_CSV row **{csv_row_index}** / **{csv_row_total}**_"
    elif csv_row_index is not None:
        head += f"\n_CSV row **{csv_row_index}**_"

    parts = [head, "", meta_table + hc_block, "", session_window, "", orch, "", prompt_sec]
    out = "\n".join(parts).strip()
    return truncate(out, max_len=DISCORD_MAX_LEN - 80)


def format_prompt_received(prompt: str, *, max_len: int = 500) -> str:
    """
    Back-compat: controller ack with prompt only (no metadata/session table).
    Prefer :func:`format_controller_label_ack` for Discord.
    """
    return format_controller_label_ack(prompt, context=None, max_prompt_len=max_len)


def format_hc_check(*, predicted: str | None, hc1: str, hc2: str) -> str:
    """
    Controller-only HC check message.
    Show only current label + HC1/HC2 (no right/wrong verdict).
    Entire message is bold.
    """
    lines = [
        "CONTROLLER",
        "",
        "HC CHECK",
        f"Predicted: {predicted or '_no prediction_'}",
        f"HC1: {hc1 or '_empty_'}",
        f"HC2: {hc2 or '_empty_'}",
    ]
    return f"**{chr(10).join(lines).strip()}**"


def format_final_answer_summary(final_labels: list[Any]) -> str:
    """One-line summary of final labels for prominent display at top of Adjudicator message."""
    if not final_labels:
        return "**⚖ Final:** _No labels_"
    parts = []
    for item in final_labels:
        if isinstance(item, dict):
            label = item.get("label", item.get("span_ref", ""))
            decision = item.get("decision", "")
            if decision:
                parts.append(f"`{label}` · decision `{decision}`")
            else:
                parts.append(f"`{label}`")
        else:
            parts.append(str(item))
    line = " · ".join(parts)
    return f"**⚖ Final:** {line}"


def format_adjudicator_discord(data: dict[str, Any]) -> str:
    """
    Full Adjudicator block: verdict line + optional Boundary-Critic weighed note + per-item
    label/decision + **untruncated** rationale (scores + Boundary Critic analysis).
    """
    parts: list[str] = []
    finals = data.get("final_labels") or []
    parts.append(format_final_answer_summary(finals))
    if data.get("boundary_critic_weighed"):
        parts.append(
            section(
                "Integration",
                "Boundary Critic output was **weighed** with `label_scores` (not argmax-only). See rationale below.",
            )
        )
    for i, item in enumerate(finals):
        if not isinstance(item, dict):
            parts.append(str(item))
            continue
        span_ref = item.get("span_ref", i)
        lbl = item.get("label", "")
        dec = item.get("decision", "")
        parts.append(
            section(
                f"Verdict (span {span_ref})",
                f"**Label:** `{lbl}`\n**Decision:** `{dec}`",
            )
        )
        rat = (item.get("rationale") or "").strip()
        if rat:
            parts.append(section("Full rationale — scores + Boundary Critic", rat))
    uncertain = data.get("uncertain") or []
    if uncertain:
        parts.append(bullet_list([str(u) for u in uncertain], header="Uncertain"))
    retry = data.get("retry")
    if retry and isinstance(retry, dict):
        parts.append(
            key_value_pairs(
                [
                    ("Retry target", retry.get("target", "")),
                    ("Instruction", str(retry.get("instruction", "") or "")),
                ],
                title="Retry",
            )
        )
    return "\n\n".join(parts) if parts else "_No output_"


def pipeline_result_discord(
    prompt: str,
    final_labels: list[Any],
    uncertain: list[Any] | None = None,
    *,
    include_prompt: bool = True,
    include_labels_table: bool = True,
) -> str:
    """
    Format one pipeline result for Discord: prompt (optional), final labels,
    and optional uncertain list. Returns a single message or truncated content.
    """
    parts = []
    if include_prompt:
        parts.append(section("Prompt", truncate(prompt, max_len=400)))
    if include_labels_table and final_labels:
        if isinstance(final_labels[0], dict):
            rows = [
                [
                    item.get("label", item.get("span_ref", i)),
                    item.get("decision", ""),
                    item.get("rationale", "")[:80],
                ]
                for i, item in enumerate(final_labels)
            ]
            parts.append(
                table_from_rows(
                    ["Label", "Decision", "Rationale"],
                    rows,
                    title="Final labels",
                )
            )
        else:
            parts.append(
                bullet_list([str(l) for l in final_labels], header="Final labels")
            )
    if uncertain:
        parts.append(bullet_list([str(u) for u in uncertain], header="Uncertain"))
    return "\n\n".join(parts)


def split_messages(text: str, max_len: int = DISCORD_MAX_LEN) -> list[str]:
    """Split long content into multiple Discord-safe messages."""
    if len(text) <= max_len:
        return [text] if text else []
    out = []
    while text:
        chunk = text[:max_len]
        last_nl = chunk.rfind("\n")
        if last_nl > max_len // 2:
            chunk = chunk[: last_nl + 1]
            text = text[last_nl + 1 :]
        else:
            text = text[max_len:]
        out.append(chunk)
    return out
