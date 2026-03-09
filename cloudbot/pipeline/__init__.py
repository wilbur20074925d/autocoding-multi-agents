"""Autocoding pipeline: signal_extractor → label_coder → boundary_critic → adjudicator."""

from .run_pipeline import run_autocoding_pipeline

__all__ = ["run_autocoding_pipeline"]
