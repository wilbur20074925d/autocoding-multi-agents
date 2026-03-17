from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TaxonomyRow:
    tier1: str
    tier2: str
    tier3: str | None
    code: str


def load_taxonomy_rows(path: str | Path) -> list[TaxonomyRow]:
    p = Path(path)
    with open(p, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows: list[TaxonomyRow] = []
        for r in reader:
            tier1 = (r.get("tier1") or "").strip()
            tier2 = (r.get("tier2") or "").strip()
            tier3 = (r.get("tier3") or "").strip()
            if not tier1 or not tier2:
                continue
            code = f"{tier1}.{tier2}.{tier3}" if tier3 else f"{tier1}.{tier2}"
            rows.append(TaxonomyRow(tier1=tier1, tier2=tier2, tier3=tier3 or None, code=code))
        return rows


def build_tier2_to_tier1(rows: list[TaxonomyRow]) -> dict[str, str]:
    """
    Map tier2 -> tier1 when unambiguous in taxonomy.
    For this taxonomy, tier2 names are unique across tier1.
    """
    out: dict[str, str] = {}
    collisions: set[str] = set()
    for r in rows:
        if r.tier2 in out and out[r.tier2] != r.tier1:
            collisions.add(r.tier2)
        else:
            out[r.tier2] = r.tier1
    for c in collisions:
        out.pop(c, None)
    return out

