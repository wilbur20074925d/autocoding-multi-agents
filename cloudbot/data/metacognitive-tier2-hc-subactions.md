# Human coder sub-actions: Metacognitive tier2 — `planning-*`

Human labels often use **`planning-*`** (e.g. `planning-give`, `planning-agree`, `planning-ask`).

**Canonical mapping:** **`planning-*`** → **`Metacognitive.planning`**.

## Critical: not Cognitive tier2

- **`planning-give` / `planning-agree`** describe **how to approach or structure the task** (steps, bullet outline, sequence for a question). That is **process / metacognitive planning**, **not** `Cognitive.solution_development`, even if the text mentions “question”, “bullet”, or “organize”.
- **`solution\development-*`** is a **different** HC strand → **`Cognitive.solution_development`** (task product / answers / labels for the response). Do **not** confuse the two when HC says **`planning-`**.

## Typical sub-actions (parallel to other strands)

| Sub-action | Meaning |
|------------|---------|
| **ask** | Ask about **how** to proceed or structure work |
| **give** | Propose a **plan** or procedure (e.g. “first list bullets, then organize…”) |
| **agree** / **disagree** | Agree or disagree with a **proposed plan** (often very short: “Yes.”) |

Short **agree** lines after a **planning-give** turn should stay **`Metacognitive.planning`** (or match the **planning** strand in HC), not default to **`Cognitive.concept_exploration`**.

See **cloudbot/data/golden-labels.md** (Metacognitive vs Cognitive boundaries).
