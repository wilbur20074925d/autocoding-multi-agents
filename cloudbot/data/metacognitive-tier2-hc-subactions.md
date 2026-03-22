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

- If the **previous** turn was **`monitoring-ask`** (e.g. a level check like “Analyzing?”) and the **current** line is **praise for the question** (“Good question.”), that is **not** more **monitoring** — it is **Socio-emotional.encouragement** (affirming the peer’s move).

- If the **previous** turn is **`monitoring-ask`** and the **current** line notes **multiple valid answers** (“There could be more than one.”), treat as **`monitoring-give`** → **`Metacognitive.monitoring`**, not **`Cognitive.solution_development`** (single-answer labeling).

See **cloudbot/data/golden-labels.md** (Metacognitive: planning vs monitoring).

---

# Metacognitive tier2 — `evaluating-*`

Human labels use **`evaluating-ask`**, **`evaluating-give`**, **`evaluating-agree`**, etc.

**Canonical mapping:** **`evaluating-*`** → **`Metacognitive.evaluating`**.

## Critical: not `Cognitive.concept_exploration`

- **`evaluating-ask`** asks whether an **output, explanation, or solution** is **good enough, clear enough, correct enough**, or whether **tool/GPT results** meet the need — that is **process judgment about quality**, **not** asking what a **concept means in theory** (the latter is **`concept\exploration-*`** → **`Cognitive.concept_exploration`**).
- Examples: *“Do the GPT results have enough detail?”* → **`evaluating-ask`**. *“What is Bloom’s taxonomy?”* → **`concept\exploration-ask`** (definition / theory), **not** evaluating.

## Typical sub-actions

| Sub-action | Meaning |
|------------|---------|
| **ask** | Ask whether quality/adequacy/correctness is sufficient |
| **give** | State a judgment about quality (e.g. “GPT results lack detail”) |
| **agree** / **disagree** | Agree/disagree with a **quality judgment** |
