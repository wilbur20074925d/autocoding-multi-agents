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


class LabelCoderOutput(TypedDict, total=False):
    labels: list[Any]
    uncertain: list[Any]
    revision_note: str | None


class BoundaryCriticOutput(TypedDict, total=False):
    challenges: list[Any]
    request_missing_evidence: list[Any]


class AdjudicatorOutput(TypedDict, total=False):
    final_labels: list[Any]
    uncertain: list[Any]
    retry: dict[str, Any] | None


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
