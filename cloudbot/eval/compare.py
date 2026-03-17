from __future__ import annotations

from dataclasses import dataclass

from cloudbot.eval.labels import LabelPattern, parse_predicted_code


@dataclass(frozen=True)
class Comparison:
    predicted: str | None
    golden_patterns: list[LabelPattern]
    is_match: bool
    matched_golden: LabelPattern | None
    mismatch_type: str | None


def _best_match_pattern(predicted: str, patterns: list[LabelPattern]) -> LabelPattern | None:
    best: LabelPattern | None = None
    best_spec = -1
    for p in patterns:
        if p.matches_code(predicted):
            spec = p.specificity()
            if spec > best_spec:
                best = p
                best_spec = spec
    return best


def compare_one(predicted: str | None, golden_patterns: list[LabelPattern]) -> Comparison:
    if not predicted:
        return Comparison(
            predicted=predicted,
            golden_patterns=golden_patterns,
            is_match=False,
            matched_golden=None,
            mismatch_type="no_prediction",
        )

    if not golden_patterns:
        return Comparison(
            predicted=predicted,
            golden_patterns=golden_patterns,
            is_match=False,
            matched_golden=None,
            mismatch_type="no_golden",
        )

    matched = _best_match_pattern(predicted, golden_patterns)
    if matched:
        return Comparison(
            predicted=predicted,
            golden_patterns=golden_patterns,
            is_match=True,
            matched_golden=matched,
            mismatch_type=None,
        )

    # classify mismatch type by component diffs against the most specific golden
    p = parse_predicted_code(predicted)
    if p is None:
        return Comparison(
            predicted=predicted,
            golden_patterns=golden_patterns,
            is_match=False,
            matched_golden=None,
            mismatch_type="invalid_predicted_format",
        )

    # pick a "reference" golden pattern: highest specificity
    ref = sorted(golden_patterns, key=lambda x: x.specificity(), reverse=True)[0]
    if ref.tier1 != p.tier1:
        mt = "tier1"
    elif ref.tier2 is not None and ref.tier2 != p.tier2:
        mt = "tier2"
    elif ref.tier3 is not None and ref.tier3 != p.tier3:
        mt = "tier3"
    else:
        mt = "other"

    return Comparison(
        predicted=predicted,
        golden_patterns=golden_patterns,
        is_match=False,
        matched_golden=None,
        mismatch_type=mt,
    )

