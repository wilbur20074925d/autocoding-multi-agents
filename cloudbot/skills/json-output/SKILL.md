---
name: json-output
description: Ensures agent outputs are valid, schema-consistent JSON for use in pipelines and downstream tools. Use when annotator, reviewer, or judge outputs must be machine-readable or consumed by workflows, APIs, or other agents.
---

# JSON Output Skill

## When to Use

- The workflow or system prompt specifies **structured JSON** as the required output format.
- Downstream steps (other agents, APIs, or scripts) will **parse** the agent’s response.
- The task requires **schema-consistent** fields (e.g., `accepted`, `score`, `reason`, `evidence`).

## Instructions

1. **Output only valid JSON**: No markdown code fences, no leading/trailing prose, unless the workflow explicitly allows a wrapper (e.g., "Respond with a JSON object inside a markdown block").
2. **Respect the schema**: If the workflow or prompt defines required fields (e.g., `verdict`, `confidence`, `items`), include them with the correct types (string, number, boolean, array, object).
3. **Escape correctly**: Use valid JSON escaping for strings (e.g., `\"`, `\n`); avoid unescaped newlines inside string values.
4. **Prefer one root object**: Emit a single JSON object or array at the top level so consumers can parse without guessing boundaries.

## Common Schemas in This Project

- **Annotator**: `{ "annotations": [...], "metadata": { ... } }`
- **Reviewer**: `{ "summary": "...", "issues": [...], "score": number }`
- **Judge**: `{ "verdict": "accepted" | "rejected", "reason": "...", "evidence_refs": [...] }`

When in doubt, match the schema referenced in the current workflow (e.g., `workflows/debate.yaml`) or in `prompts/system.md`.

## Examples

- **Good**: `{"verdict": "accepted", "reason": "All criteria met.", "evidence_refs": ["E1", "E2"]}`
- **Avoid**: Wrapping in markdown without being asked, or omitting required fields.
