# Autocoding agents

Four-agent pipeline. Each agent has an `AGENTS.md` in its folder; skills live in `.cursor/skills/`.

| Agent | Folder | Skill |
|-------|--------|--------|
| **Signal Extractor** | [signal-extractor/](signal-extractor/) | `.cursor/skills/signal-extractor/SKILL.md` |
| **Label Coder** | [label-coder/](label-coder/) | `.cursor/skills/label-coder/SKILL.md` |
| **Boundary Critic** | [boundary-critic/](boundary-critic/) | `.cursor/skills/boundary-critic/SKILL.md` |
| **Adjudicator** | [adjudicator/](adjudicator/) | `.cursor/skills/adjudicator/SKILL.md` |

Workflow: `workflows/autocoding.yaml`. Flow: project root [FLOW.md](../../FLOW.md).

Deprecated (redirect only): [annotator/](annotator/), [reviewer/](reviewer/).
