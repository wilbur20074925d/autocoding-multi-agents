# Autocoding Pipeline: Flow and Agent Interactions

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
