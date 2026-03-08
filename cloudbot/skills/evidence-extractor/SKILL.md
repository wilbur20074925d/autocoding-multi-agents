---
name: evidence-extractor
description: Extracts and structures evidence from text or code for use in review and judgment. Use when the workflow requires cited evidence, claim–evidence pairs, or span-based references for annotator, reviewer, or judge agents.
---

# Evidence Extractor Skill

## When to Use

- The task requires **cited evidence** (quotes, line ranges, or spans) from source material.
- Annotator, Reviewer, or Judge output must reference **claim–evidence pairs** or **supporting excerpts**.
- The workflow expects **span-based references** (e.g., character offsets, line numbers, or code regions).

## Instructions

1. **Identify evidence units**: For each claim, finding, or conclusion, locate the exact span(s) in the source that support it (text snippet, file:line, or offset range).
2. **Structure output**: Represent each piece of evidence with:
   - `claim` or `finding`: short description of what is being supported
   - `source`: reference to the source (e.g., file path, section title, or "input text")
   - `span`: exact excerpt, line range, or character range
   - `type` (optional): e.g., `quote`, `code`, `citation`
3. **Preserve traceability**: Ensure the Judge or Reviewer can map every verdict or review point back to at least one evidence entry.
4. **Prefer concise excerpts**: Quote only the minimal span needed to support the claim; avoid large copy-pastes.

## Output Format

Use the project’s preferred structure (e.g., list of objects with `claim`, `source`, `span`). When **json-output** is also in use, emit evidence as a JSON array or nested field so downstream agents can consume it programmatically.

## Examples

- **Text**: "The policy states X" → evidence: `{ "claim": "policy states X", "source": "document", "span": "The policy states X." }`
- **Code**: "Function F is used in module M" → evidence: `{ "claim": "F used in M", "source": "M.py", "span": "lines 12–15", "excerpt": "def F(...): ..." }`
