# Autocoding Pipeline: Flow and Agent Interactions

## Input: Single Prompt or CSV of Prompts

The user can send **either**:

1. **A single prompt** (e.g. pasted text or one message)  
   → Run the full pipeline once on that prompt.

2. **A CSV file that contains only user prompts** (one prompt per row, e.g. a column named `prompt` or `sentence`, or no header with one prompt per line)  
   → **Read the CSV and process prompts one by one.** The next prompt **must not** start until the previous one has **completed** (Signal Extractor → Label Coder → Boundary Critic → Label Coder revision → Adjudicator). No parallel or batched processing of CSV rows.

To load a prompts-only CSV in code, use **`cloudbot/data/load_prompts_csv.py`** (returns a list of prompt strings in order).

## Pipeline Overview

```
Prompt + Context metadata (group, timestamp-mm, people, context)
  ↓
Signal Extractor  (evidence spans, candidate signals, ambiguity)
  ↓
Label Coder       (draft labels from evidence)
  ↓
Boundary Critic   (challenge coder)
  ↓
Label Coder       (revise once, if challenged)
  ↓
Adjudicator       (final arbitration)
```

**Context metadata** (from training data in **cloudbot/data/training/**: **group**, **timestamp-mm**, **people**, and **context**) is passed to **all agents** so they can take session identity, participants, timing, and condition into account when extracting evidence, assigning labels, challenging boundaries, and making final decisions.

## Agent Roles and Interactions

| From | To | Content |
|------|-----|--------|
| **Signal Extractor** | Label Coder | Extracted evidence spans, candidate signals, ambiguity flags. No final labels. |
| **Label Coder** | Boundary Critic | Original prompt + context metadata + assigned labels + evidence used. |
| **Boundary Critic** | Label Coder | Challenges: inflated label?, cognitive vs metacognitive?, evidence explicit?, better alternative?, uncertain? |
| **Boundary Critic** | Signal Extractor | Request missing evidence (when evidence is insufficient). |
| **Adjudicator** | (reads all) | Original prompt, context metadata (group, timestamp-mm, people, context), Signal Extractor output, Label Coder output, Boundary Critic output. |
| **Adjudicator** | Boundary Critic or Label Coder | Optional one-round retry. |

## Label Taxonomy

The 3-tier structure is defined in **cloudbot/data/label-taxonomy.csv**:

- **Tier 1**: Cognitive | Metacognitive | Coordinative | Socio-emotional
- **Tier 2**: Sub-category (e.g. concept_exploration, planning, coordinate_participants)
- **Tier 3**: Action (ask, answer, agree, disagree, give, build_on) or socio-emotional type

All agents must use this taxonomy; the Signal Extractor only suggests *candidate* labels and never commits final codes.

## Skills Location

Each agent has a dedicated Cursor skill in this project:

- `.cursor/skills/signal-extractor/SKILL.md`
- `.cursor/skills/label-coder/SKILL.md`
- `.cursor/skills/boundary-critic/SKILL.md`
- `.cursor/skills/adjudicator/SKILL.md`

Invoke the pipeline by running agents in order and passing outputs as inputs to the next. Use the skill when acting as that agent (e.g. "as Signal Extractor", "as Label Coder").

## Integrations

- **Discord**: Format pipeline output for Discord (tables, sections, code blocks) using `cloudbot/discord/format.py` so messages look structured and readable. See **INTEGRATIONS.md**.
- **Google Sheets**: After each prompt is fully processed, append one row to the [Multi-Agent Autocoding sheet](https://docs.google.com/spreadsheets/d/1atmf7D_qXQzEUVmx82TFv9ztyzkPmG1FSYcFPIyF6rc/edit?usp=sharing) via `cloudbot/integrations/sheets.py` (`append_result`). See **INTEGRATIONS.md** for setup (service account, sharing the sheet).
