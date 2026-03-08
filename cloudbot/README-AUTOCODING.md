# Autocoding Multi-Agent System

Four-agent pipeline for labeling user prompts into **Cognitive**, **Metacognitive**, **Coordinative**, and **Socio-emotional** interactions using a 3-tier taxonomy.

**Input:** The user may send a **single prompt** (run the pipeline once) or a **CSV file containing only prompts** (one per row). For CSV, process prompts **one by one**; do not start the next prompt until the previous one has fully completed (through Adjudicator). Use `cloudbot/data/load_prompts_csv.py` to load prompts from a CSV.

**Integrations:** Use **Discord** formatting (`cloudbot/discord/format.py`) for structured, readable messages (tables, sections, code). After each prompt completes, **update the [Google Sheet](https://docs.google.com/spreadsheets/d/1atmf7D_qXQzEUVmx82TFv9ztyzkPmG1FSYcFPIyF6rc/edit?usp=sharing)** with `cloudbot/integrations/sheets.append_result()`. See **INTEGRATIONS.md** for setup.

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
