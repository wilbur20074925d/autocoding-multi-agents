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


def bullet_list(items: list[str], *, header: str | None = None) -> str:
    """Format a bullet list. Use • for a clean look."""
    lines = [f"• {item}" for item in items]
    body = "\n".join(lines)
    if header:
        return section(header, body)
    return body


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
