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

---

# Metacognitive tier2 — `monitoring-*`

Human labels use **`monitoring-ask`**, **`monitoring-answer`**, **`monitoring-agree`**, etc.

**Canonical mapping:** **`monitoring-*`** → **`Metacognitive.monitoring`** (progress, pace, next step — *not* procedure planning).

## Consistency with `planning-*`

- If the **previous** turn was **`monitoring-ask`** (e.g. “Do you want to move on to the multiple-choice questions?”) and the **current** line is a short **assent** (“I think it’s okay.”, “Yes.”), the current line is still part of the **monitoring** thread → **`Metacognitive.monitoring`**, not **`Metacognitive.planning`**, unless the speaker is clearly proposing a **new** procedure (steps, bullets, “first we…”).

See **cloudbot/data/golden-labels.md** (Metacognitive: planning vs monitoring).
