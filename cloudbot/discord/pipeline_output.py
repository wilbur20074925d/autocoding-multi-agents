"""
Contract for structured output from the autocoding pipeline (OpenClaw runtime).

The pipeline runs: signal_extractor → label_coder → boundary_critic → label_coder (revision) → adjudicator.
The runtime is expected to return a single dict with per-agent outputs so the Discord
dispatcher can send each slice to the corresponding bot identity.
"""

from __future__ import annotations

from typing import Any, TypedDict


class SignalExtractorOutput(TypedDict, total=False):
    evidence_spans: list[Any]
    candidate_signals: list[Any]
    ambiguity: list[Any]
    # Session window + cognitive tilt (CE vs SD) from neighbors in same group
    session_overview: dict[str, Any]


class LabelCoderOutput(TypedDict, total=False):
    labels: list[Any]
    uncertain: list[Any]
    revision_note: str | None
    # Full taxonomy scores (every Tier1.tier2 code), plus display helpers for Boundary Critic / UI
    label_scores: dict[str, float]
    label_scores_ranked: list[dict[str, Any]]
    scores_close: bool
    label_scores_margin_top2: float
    label_scores_display: str


class BoundaryCriticOutput(TypedDict, total=False):
    challenges: list[Any]
    request_missing_evidence: list[Any]


class AdjudicatorOutput(TypedDict, total=False):
    final_labels: list[Any]
    uncertain: list[Any]
    retry: dict[str, Any] | None
    # Set by pipeline postprocess: structured analysis (scores + Boundary Critic)
    adjudication_analysis: str
    boundary_critic_weighed: bool
    # Event–act alignment across adjacent turns (Ethnography of Communication)
    consistency_checking: dict[str, Any]
    consistency_llm_retry_completed: bool


class PipelineOutput(TypedDict, total=False):
    """
    Full pipeline result from OpenClaw autocoding workflow.

    Keys must match workflow agent ids so the dispatcher can route by role.
    label_coder is the final revised output (after Boundary Critic); adjudicator
    sees that plus boundary_critic when making the final decision.
    """
    prompt: str
    context: dict[str, Any]
    signal_extractor: SignalExtractorOutput
    label_coder: LabelCoderOutput
    boundary_critic: BoundaryCriticOutput
    adjudicator: AdjudicatorOutput
