"""
In-memory Discord session buffer: same channel, same `group`, **contiguous** in send order.

A **session** here is a maximal streak of labeled turns that share the same `group`
value (empty string groups together). When a different `group` appears, the streak
breaks; the next label only sees neighbors from the **current** streak immediately
before it — mirroring “连续同一组” in chat order.

Used for `session_prompts_before` in single-message `Label this prompt` flow.
`session_prompts_after` stays empty (future turns are unknown until they arrive).
"""

from __future__ import annotations

import os
import re

# channel_id -> list of (group, prompt) in **arrival order** (oldest first)
_buffer: dict[int, list[tuple[str, str]]] = {}

_DEFAULT_MAX_BUFFER = 256
_DEFAULT_MAX_BEFORE = 6

_LABEL_TRIGGER_PREFIX = re.compile(
    r"^\s*label\s+this\s+prompt\s*[:：]?\s*",
    re.IGNORECASE,
)

# Optional metadata lines **before** the prompt body (first non-matching line starts prompt)
_META_LINE = re.compile(
    r"^\s*(group|timestamp|timestamp-mm|timestamp_mm|people|context|hc1|hc2)\s*[:=]\s*(.+?)\s*$",
    re.IGNORECASE,
)


def _max_buffer_size() -> int:
    raw = (os.environ.get("DISCORD_SESSION_BUFFER_MAX") or "").strip()
    if raw.isdigit():
        return max(16, min(10_000, int(raw)))
    return _DEFAULT_MAX_BUFFER


def _max_before() -> int:
    raw = (os.environ.get("DISCORD_SESSION_MAX_NEIGHBORS") or "").strip()
    if raw.isdigit():
        return max(1, min(32, int(raw)))
    return _DEFAULT_MAX_BEFORE


def normalize_group(group: str | None) -> str:
    return (group or "").strip()


def parse_discord_label_message(text: str) -> tuple[str, dict[str, str]]:
    """
    Strip the trigger phrase and optional header metadata; return (prompt, meta).

    Metadata lines (only at the **top**, before the first non-meta line)::

        group: G12
        timestamp-mm: 10:05
        people: 2
        context: discussion
        HC1: Cognitive.concept_exploration

    Keys are case-insensitive; `timestamp-mm` and `timestamp_mm` map to `timestamp`
    for the pipeline. `hc1` / `hc2` map to `HC1` / `HC2`.
    """
    t = (text or "").strip()
    t = _LABEL_TRIGGER_PREFIX.sub("", t, count=1).strip()
    meta: dict[str, str] = {}
    lines = t.splitlines()
    i = 0
    while i < len(lines):
        raw_line = lines[i]
        line = raw_line.strip()
        if not line:
            i += 1
            continue
        m = _META_LINE.match(line)
        if not m:
            break
        key = m.group(1).lower().replace("-", "_")
        val = m.group(2).strip()
        if key in ("timestamp", "timestamp_mm"):
            meta["timestamp"] = val
        elif key == "hc1":
            meta["HC1"] = val
        elif key == "hc2":
            meta["HC2"] = val
        else:
            meta[key] = val
        i += 1
    prompt = "\n".join(lines[i:]).strip()
    return prompt, meta


def contiguous_neighbors_before(
    channel_id: int,
    current_group: str | None,
    *,
    max_before: int | None = None,
) -> list[str]:
    """
    Walk the channel buffer from **newest** backward while `group` matches
    `current_group` (normalized); return up to `max_before` prompts immediately
    before the current turn, in **chronological** order (oldest → newest).
    """
    cap = max_before if max_before is not None else _max_before()
    g = normalize_group(current_group)
    buf = _buffer.get(int(channel_id)) or []
    streak_newest_first: list[str] = []
    for group, prompt in reversed(buf):
        if normalize_group(group) != g:
            break
        p = prompt.strip()
        if p:
            streak_newest_first.append(p)
    take = streak_newest_first[:cap]
    return list(reversed(take))


def record_labeled_turn(channel_id: int, group: str | None, prompt: str) -> None:
    """Append a completed label turn (after pipeline success). Trims old tail."""
    cid = int(channel_id)
    g = normalize_group(group)
    p = (prompt or "").strip()
    if not p:
        return
    lst = _buffer.setdefault(cid, [])
    lst.append((g, p))
    max_sz = _max_buffer_size()
    if len(lst) > max_sz:
        del lst[: len(lst) - max_sz]


def clear_channel_buffer(channel_id: int) -> None:
    """Testing / admin: drop history for a channel."""
    _buffer.pop(int(channel_id), None)


def buffer_snapshot(channel_id: int) -> list[tuple[str, str]]:
    """Testing: copy of buffer for channel."""
    return list(_buffer.get(int(channel_id), []))
