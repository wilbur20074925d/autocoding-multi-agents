# Autocoding Multi-Agent System

Four-agent pipeline for labeling user prompts into **Cognitive**, **Metacognitive**, **Coordinative**, and **Socio-emotional** interactions using a 3-tier taxonomy.

## Quick reference

| Artifact | Location |
|----------|----------|
| **Label taxonomy (3-tier)** | `cloudbot/data/label-taxonomy.csv` |
| **Pipeline flow & interactions** | `FLOW.md` |
| **Signal Extractor skill** | `.cursor/skills/signal-extractor/SKILL.md` |
| **Label Coder skill** | `.cursor/skills/label-coder/SKILL.md` |
| **Boundary Critic skill** | `.cursor/skills/boundary-critic/SKILL.md` |
| **Adjudicator skill** | `.cursor/skills/adjudicator/SKILL.md` |

## Pipeline order

1. **Signal Extractor** → evidence spans, candidate signals, ambiguity (no final labels).
2. **Label Coder** → draft labels from evidence.
3. **Boundary Critic** → challenge coder (and optionally request missing evidence from Signal Extractor).
4. **Label Coder** → one revision after challenge.
5. **Adjudicator** → final arbitration (accept coder / accept critic / combine / uncertain; optional one-round retry).

## Taxonomy (Tier 1)

- **Cognitive**: task content — analyzing, exploring, explaining (what).
- **Metacognitive**: process — planning, evaluating, monitoring (how to solve).
- **Coordinative**: collaboration — who does what, how to share (how to collaborate).
- **Socio-emotional**: emotional, encouragement, forming sense of group.

Full codes are `tier1.tier2.tier3` (e.g. `Cognitive.concept_exploration.ask`). See `cloudbot/data/label-taxonomy.csv` and training data in `cloudbot/data/training/` for structure and examples.
