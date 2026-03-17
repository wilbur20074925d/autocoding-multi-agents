from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LabelPattern:
    """
    A possibly-partial human label pattern used for matching.

    - If only tier1 is present (e.g. "cognitive"), it matches any prediction with that tier1.
    - If tier1+tier2 present, it matches any prediction with that tier1+tier2 (tier3 ignored).
    - If tier1+tier2+tier3 present, it's an exact code match.
    """

    tier1: str
    tier2: str | None = None
    tier3: str | None = None

    def specificity(self) -> int:
        return 1 + (1 if self.tier2 else 0) + (1 if self.tier3 else 0)

    def matches_code(self, predicted_code: str) -> bool:
        p = parse_predicted_code(predicted_code)
        if p is None:
            return False
        if self.tier1 != p.tier1:
            return False
        if self.tier2 is not None and self.tier2 != p.tier2:
            return False
        if self.tier3 is not None and self.tier3 != p.tier3:
            return False
        return True


@dataclass(frozen=True)
class ParsedCode:
    tier1: str
    tier2: str
    tier3: str | None

    def code(self) -> str:
        return f"{self.tier1}.{self.tier2}.{self.tier3}" if self.tier3 else f"{self.tier1}.{self.tier2}"


def parse_predicted_code(code: str) -> ParsedCode | None:
    parts = [p.strip() for p in (code or "").split(".") if p.strip()]
    if len(parts) < 2:
        return None
    tier1 = parts[0]
    tier2 = parts[1]
    tier3 = parts[2] if len(parts) >= 3 else None
    return ParsedCode(tier1=tier1, tier2=tier2, tier3=tier3)


def titlecase_tier1(tier1: str) -> str:
    t = (tier1 or "").strip()
    if not t:
        return t
    return t[0].upper() + t[1:].lower()

