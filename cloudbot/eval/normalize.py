from __future__ import annotations

import re
from dataclasses import dataclass

from cloudbot.eval.labels import LabelPattern, titlecase_tier1


@dataclass(frozen=True)
class NormalizationResult:
    raw: str
    patterns: list[LabelPattern]
    warnings: list[str]


_TIER1_ALIASES = {
    "cognitive": "Cognitive",
    "metacognitive": "Metacognitive",
    "coordinative": "Coordinative",
    "socio-emotional": "Socio-emotional",
    "socioemotional": "Socio-emotional",
    "social-emotional": "Socio-emotional",
}


def normalize_human_label(
    raw_label: str,
    *,
    tier2_to_tier1: dict[str, str],
) -> NormalizationResult:
    """
    Convert training CSV label strings into canonical-ish match patterns.

    Supports:
    - Canonical codes: Tier1.tier2 (latest) or Tier1.tier2.tier3 (legacy/auxiliary)
    - Tier1-only: "cognitive" / "metacognitive" / "coordinative" / "socio-emotional"
    - Shorthand: "evaluating-give", "planning-ask", "concept_exploration-build_on"
    - The legacy escaped training CSV style: "concept\\exploration-build\\on"
    """
    raw = (raw_label or "").strip()
    if not raw:
        return NormalizationResult(raw=raw_label, patterns=[], warnings=["empty label"])

    warnings: list[str] = []
    label = raw

    # Unescape old CSV encoding: concept\exploration-build\on
    label = label.replace("\\", "_")
    label = re.sub(r"\s+", "", label)
    label = label.replace("/", "_")

    # Canonical Tier1.tier2(.tier3)
    if "." in label:
        parts = [p for p in label.split(".") if p]
        if len(parts) >= 2:
            tier1 = titlecase_tier1(parts[0])
            tier2 = parts[1]
            tier3 = parts[2] if len(parts) >= 3 else None
            # Latest scheme is Tier1.tier2; treat any provided tier3 as auxiliary.
            # This ensures gold labels remain compatible with 3-tier predictions.
            return NormalizationResult(raw=raw_label, patterns=[LabelPattern(tier1=tier1, tier2=tier2)], warnings=[])

    # Tier1-only
    lower = label.lower()
    if lower in _TIER1_ALIASES:
        return NormalizationResult(raw=raw_label, patterns=[LabelPattern(tier1=_TIER1_ALIASES[lower])], warnings=[])

    # Shorthand tier2-tier3 (or tier2_tier3), infer tier1 from taxonomy.
    # Latest golden labels are tier2-only, so we drop tier3 after inference.
    m = re.match(r"^(?P<tier2>[a-z_]+)[-_](?P<tier3>[a-z_]+)$", lower)
    if m:
        tier2 = m.group("tier2")
        tier3 = m.group("tier3")

        # normalize tier3 variants
        tier3 = tier3.replace("build_on", "build_on")
        if tier3 in ("buildon", "build_on", "build"):
            tier3 = "build_on"
        if tier3 == "formingsenseof":
            tier3 = "forming_sense_of"

        tier1 = tier2_to_tier1.get(tier2)
        if not tier1:
            warnings.append(f"cannot infer tier1 from tier2='{tier2}' (unknown tier2)")
            return NormalizationResult(raw=raw_label, patterns=[], warnings=warnings)

        return NormalizationResult(raw=raw_label, patterns=[LabelPattern(tier1=tier1, tier2=tier2)], warnings=warnings)

    warnings.append(f"unrecognized label format: '{raw}'")
    return NormalizationResult(raw=raw_label, patterns=[], warnings=warnings)


def normalize_human_labels(
    raw_labels: list[str],
    *,
    tier2_to_tier1: dict[str, str],
) -> tuple[list[LabelPattern], list[str]]:
    patterns: list[LabelPattern] = []
    warnings: list[str] = []
    for raw in raw_labels:
        res = normalize_human_label(raw, tier2_to_tier1=tier2_to_tier1)
        patterns.extend(res.patterns)
        warnings.extend([f"{raw!r}: {w}" for w in res.warnings])
    return patterns, warnings

