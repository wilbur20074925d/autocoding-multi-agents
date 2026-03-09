"""
Main controller for 1 orchestrator + 4 display bots.

  - Receives Discord message (user prompt)
  - Runs autocoding pipeline (cloudbot.pipeline by default; or pass run_pipeline)
  - Dispatches structured output to 4 Discord bot identities
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from .dispatcher import prepare_four_bot_messages

try:
    from cloudbot.pipeline import run_autocoding_pipeline as _default_pipeline
except ImportError:
    try:
        from pipeline import run_autocoding_pipeline as _default_pipeline
    except ImportError:
        _default_pipeline = None


def run_autocoding_pipeline_placeholder(
    prompt: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Placeholder when pipeline package is not used. Prefer run_autocoding_pipeline from cloudbot.pipeline."""
    return {
        "prompt": prompt,
        "context": context or {},
        "signal_extractor": {"evidence_spans": [], "candidate_signals": [], "ambiguity": []},
        "label_coder": {"labels": [], "uncertain": [], "revision_note": None},
        "boundary_critic": {"challenges": [], "request_missing_evidence": []},
        "adjudicator": {"final_labels": [], "uncertain": [], "retry": None},
    }


async def handle_discord_message(
    prompt: str,
    channel_id: str | int,
    *,
    context: dict[str, Any] | None = None,
    run_pipeline: Callable[[str, dict[str, Any] | None], dict[str, Any]]
    | None = None,
    send_as_bot: Callable[[str, str | int, str], Any] | None = None,
) -> list[tuple[str, str]]:
    """
    Process one user prompt and post 4 bot messages in order.

    Args:
        prompt: User prompt text (from Discord message).
        channel_id: Discord channel (or thread) to post to.
        context: Optional context metadata (group, timestamp-mm, people, context).
        run_pipeline: Function (prompt, context) -> pipeline_output. Default: placeholder.
        send_as_bot: Async or sync (role_id, channel_id, content) -> None. Default: no-op.

    Returns:
        List of (role_id, content) that were (or would be) posted.
    """
    run_pipeline = run_pipeline or _default_pipeline or run_autocoding_pipeline_placeholder
    pipeline_output = run_pipeline(prompt, context)
    if "prompt" not in pipeline_output:
        pipeline_output["prompt"] = prompt

    messages = prepare_four_bot_messages(
        pipeline_output,
        include_prompt_in_first=True,
    )

    if send_as_bot:
        for role_id, content in messages:
            result = send_as_bot(role_id, channel_id, content)
            if asyncio.iscoroutine(result):
                await result

    return messages
