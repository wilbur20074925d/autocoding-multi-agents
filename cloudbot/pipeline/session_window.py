"""
Session window helpers: neighboring utterances in the same group (CSV / chat order)
for disambiguating Cognitive tier2 and for Signal Extractor overview.
"""

from __future__ import annotations

import re
from typing import Any


def _timestamp_sort_key(row: dict[str, Any]) -> tuple[int, int]:
    """
    Sort key for session ordering. Prefers timestamp-mm / timestamp columns.
    Falls back to row index in file.
    """
    ts = (
        str(row.get("timestamp") or row.get("timestamp-mm") or row.get("timestamp_mm") or "")
        .strip()
    )
    if not ts:
        return (999_999, 0)
    # HH:MM:SS or H:MM:SS or MM:SS (assume minutes if one colon)
    m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", ts)
    if m:
        a, b, c = m.group(1), m.group(2), m.group(3)
        if c is not None:
            return (int(a) * 3600 + int(b) * 60 + int(c), 0)
        # Could be MM:SS only
        if int(a) < 60 and int(b) < 60:
            return (int(a) * 60 + int(b), 0)
        return (int(a) * 3600 + int(b) * 60, 0)
    # digits only
    if ts.isdigit():
        return (int(ts), 0)
    return (500_000, hash(ts) % 10_000)


def build_csv_session_neighbors(
    rows: list[dict[str, Any]],
    row_index: int,
    group: str,
    *,
    max_each: int = 6,
) -> tuple[list[str], list[str]]:
    """
    For a CSV row at `row_index` (0-based), return utterances in the same `group`
    before / after in time order (timestamp when present, else file order within group).

    Used to populate `session_prompts_before` / `session_prompts_after` in pipeline context.
    """
    g = (group or "").strip()
    if not g or row_index < 0 or row_index >= len(rows):
        return [], []

    indexed: list[tuple[int, dict[str, Any]]] = [
        (i, r) for i, r in enumerate(rows) if (str(r.get("group") or "").strip() == g)
    ]
    if len(indexed) <= 1:
        return [], []

    indexed.sort(key=lambda ir: (_timestamp_sort_key(ir[1]), ir[0]))

    pos = None
    for p, (i, _) in enumerate(indexed):
        if i == row_index:
            pos = p
            break
    if pos is None:
        return [], []

    def _text(r: dict[str, Any]) -> str:
        return (str(r.get("sentence") or r.get("prompt") or "")).strip()

    before: list[str] = []
    for j in range(max(0, pos - max_each), pos):
        t = _text(indexed[j][1])
        if t:
            before.append(t)

    after: list[str] = []
    for j in range(pos + 1, min(len(indexed), pos + 1 + max_each)):
        t = _text(indexed[j][1])
        if t:
            after.append(t)

    return before, after
