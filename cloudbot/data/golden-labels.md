# Golden Labels: Primary Source of Truth

## Policy

- **Golden labels are primary.** When human-coder labels (e.g. HC1, HC2) or any explicitly provided gold labels exist for a prompt, they are the **main reference** for what the correct code should be. The pipeline should **align outputs to golden labels** when available.
- **Training data is auxiliary (辅助).** Training examples in `cloudbot/data/training/` are for **calibration and context only**—to help agents recognize patterns and boundaries. They do **not** override golden labels. Use training to inform extraction and coding, but do not treat training as the source of truth when a golden label is provided for the current prompt.

## When Golden Labels Are Used

- **Evaluation:** Compare pipeline final labels to golden labels (HC1/HC2 or provided `label`).
- **Label Coder:** When a golden label is passed with the prompt, the Label Coder should **target that label** and justify it from evidence; if evidence does not support it, the coder should note the discrepancy and still prefer the golden label when the prompt is explicitly labeled.
- **Adjudicator:** When golden labels are available, prefer final decisions that **match the golden label** when the evidence and critic allow it; use critic feedback to catch drift from gold, not to replace gold without reason.

---

## Precise Golden-Label Criteria

Use these criteria to **interpret and apply** labels consistently. All agents should use these boundaries; the Boundary Critic in particular must use them to challenge misapplication.

### Label (Tier 1) boundaries (when to use which label)

| Label | Meaning | When to use |
|--------|---------|-------------|
| **Cognitive** | **Task content:** concepts and solutions related to the learning task. | Utterance is about *what* the task content is—concepts/definitions or solution content/correctness. |
| **Metacognitive** | **Process:** planning, monitoring, evaluating the task. | Utterance is about *how* to do the task—planning steps, checking progress, or evaluating outputs. |
| **Coordinative** | **Coordination:** who does what, and how the group works together. | Utterance is about role allocation, turn-taking, procedures, tools, and logistics. |
| **Socio-emotional** | **Affect/relationship:** emotions, encouragement, self-disclosure. | Utterance expresses feelings, support/praise, or personal disclosure (experience, unfamiliarity). |

**Critical distinctions:**

- **Cognitive vs Metacognitive:** If the utterance is about *the content* of the task (e.g. "what is Bloom's taxonomy?", "this answer is correct") → **Cognitive**. If it is about *how we’re doing the task* (e.g. "should we move on?", "are we on track?") → **Metacognitive**.
- **Coordinative vs Socio-emotional:** If it’s about *task structure* (who does what, how we run the session) → **Coordinative**. If it’s about *feelings, support, or belonging* → **Socio-emotional**.

### Tier 2 (sub-category) — precise use

- **Cognitive:** `concept_exploration` = discuss/clarify concepts; `solution_development` = discuss/clarify solutions/answers.
- **Metacognitive:** `planning` = plan procedures/goal setting; `monitoring` = check progress/next steps vs plan; `evaluating` = assess information quality and outcomes.
- **Coordinative:** `coordinate_participants` = allocate tasks/roles; `coordinate_procedures` = manage workflow/turn-taking/technical logistics.
- **Socio-emotional:** `emotional_expression` = feelings/reactions; `encouragement` = praise/cheer; `self_disclosure` = personal experience/unfamiliarity.

### Label format

- **Canonical form (latest):** `Tier1.tier2` (e.g. `Cognitive.concept_exploration`, `Metacognitive.monitoring`).
- **Backward compatibility:** If a prediction includes a third segment (e.g. `Cognitive.concept_exploration.ask`), evaluation should still match a golden `Cognitive.concept_exploration` (tier3 is ignored when gold is tier2-only).
- **From training CSV:** Human coders may use shorthand. Map to canonical form using **label-taxonomy.csv** as the single list of valid codes (see mapping table below).

---

## Decision rules (for accuracy)

Apply in order when in doubt:

1. **Tier1 first:** Is it about *content* (what) → Cognitive. *Process* (how we do it) → Metacognitive. *Who does what / logistics* → Coordinative. *Feelings, support, belonging* → Socio-emotional.
2. **Cognitive tier2:** About *concepts/definitions/learning* → concept_exploration. About *solutions, answers, task product* (naming, defining, analyzing the solution) → solution_development.
3. **Metacognitive tier2:** About *how to approach or plan* → planning. About *progress, pace, on track* → monitoring. About *judging quality of solution/output* → evaluating.
4. **No blanket default:** Do **not** assign `Cognitive.concept_exploration` unless the utterance is **mainly** about clarifying or exploring **concepts/meanings**. Laughter, thanks, coordination, metacognitive process talk, or solution/option talk should use the matching codes above—not concept_exploration as a catch-all.

---

## Edge cases and examples

| Utterance / situation | Correct direction | Common error to avoid |
|------------------------|-------------------|------------------------|
| "What is Bloom's taxonomy?" | Cognitive.concept_exploration | Not Metacognitive.planning |
| "The answer should be the last one." | Cognitive.solution_development | Not Metacognitive.evaluating (unless it is explicitly judging output quality) |
| "We can first search for the answer." | Metacognitive.planning | Not Coordinative.coordinate_procedures (unless it’s about logistics/turn-taking) |
| "We can move to the next question." | Metacognitive.monitoring | Not Coordinative.coordinate_procedures |
| "GPT results lack detail." | Metacognitive.evaluating | Not Cognitive.solution_development |
| "We can divide the task into three parts." | Coordinative.coordinate_participants | Not Metacognitive.planning |
| "You go first." | Coordinative.coordinate_procedures | Not Socio-emotional.encouragement |
| "That’s hilarious!" | Socio-emotional.emotional_expression | Not Cognitive |
| "Hahaha" / "哈哈哈" / "LOL" / "hhh" | Socio-emotional.emotional_expression | Not encouragement |
| "Thank you!" | Socio-emotional.encouragement | Not emotional_expression |
| "It's okay." | Metacognitive.planning | Not Socio-emotional.encouragement |
| "No, no, it is different." | Metacognitive.planning | Not Socio-emotional.emotional_expression |
| "I’ve worked as a TA before." | Socio-emotional.self_disclosure | Not Cognitive.concept_exploration |

---

## Golden examples by label (canonical dotted codes)

Use these as the **default reference examples** for consistent human coding and for Boundary Critic challenges. Each block contains:

- **Positive**: utterances that should be labeled as the code.
- **Near-miss**: common confusions and the correct alternative label.

### Cognitive.concept_exploration

- **Positive**
  - "What is Bloom's taxonomy?"
  - "What does 'metacognitive' mean here?"
  - "Can you explain what 'coordinate procedures' refers to?"
- **Near-miss (NOT this)**
  - "How should we solve this question?" → Metacognitive.planning
  - "Are we on the right track?" → Metacognitive.monitoring

### Cognitive.solution_development

- **Positive**
  - "The answer should be the last one."
  - "Option C is best."
  - "Let's phrase our final answer like this."
- **Near-miss**
  - "Should we split the work?" → Coordinative.coordinate_participants

### Metacognitive.planning

- **Positive**
  - "How should we solve this question?"
  - "What steps should we take first?"
  - "Should we start by defining the concept or by checking options?"
  - "It's okay." (legacy `planning-agree` maps to planning)
  - "No, no, it is different." (legacy planning-disagree maps to planning)
- **Near-miss (NOT this)**
  - "Are we on the right track?" → Metacognitive.monitoring
  - "Is our solution correct?" / "Does our explanation make sense?" → Metacognitive.evaluating

### Metacognitive.monitoring

- **Positive**
  - "Are we on the right track?"
  - "How much progress have we made?"
  - "Do we need to speed up?"
- **Near-miss**
  - "Do you think this solution is ok?" → Metacognitive.evaluating

### Metacognitive.evaluating

- **Positive**
  - "Do you think this solution is ok?"
  - "Does our explanation make sense?"
  - "Is our final answer strong enough?"
- **Near-miss**
  - "Are we on the right track?" → Metacognitive.monitoring

### Coordinative.coordinate_participants

- **Positive**
  - "Who should do what?"
  - "Can you handle the summary while I do the examples?"
  - "Do you want to present or should I?"
- **Near-miss**
  - "How should we solve this?" → Metacognitive.planning

### Coordinative.coordinate_procedures

- **Positive**
  - "How should we share information?"
  - "Where do we write the final answer—Google Doc or chat?"
  - "Should we use bullet points or paragraph format?"
- **Near-miss**
  - "Which option is correct?" → Cognitive.solution_development

### Socio-emotional.emotional_expression

- **Positive**
  - "Hahaha."
  - "哈哈哈"
  - "LOL"
  - "I'm confused."
  - "I'm frustrated."
  - "I'm nervous about this."
- **Near-miss**
  - "Great job!" → Socio-emotional.encouragement
  - "We're behind schedule." → Metacognitive.monitoring

### Socio-emotional.encouragement

- **Positive**
  - "Good job."
  - "Don't worry, we can do it."
  - "Nice idea—keep going."
- **Near-miss**
  - "Yes, because..." → Usually Cognitive.* or Metacognitive.* (not encouragement)
  - "It's okay." → Metacognitive.planning (legacy planning-agree)

### Socio-emotional.self_disclosure

- **Positive**
  - "I’ve worked as a TA before."
  - "I’m not familiar with this topic."
  - "I’ve never done this kind of task."
- **Near-miss**
  - "Let's split tasks." → Coordinative.coordinate_participants

---

## Training shorthand → canonical mapping

Use **label-taxonomy.csv** for the exact list. Common mappings:

| Training shorthand | Canonical (example) |
|--------------------|---------------------|
| concept\exploration-ask / concept\exploration-answer / concept\exploration-give / ... | Cognitive.concept_exploration (tier3 ignored in latest scheme) |
| solution\development-* | Cognitive.solution_development |
| planning-* / planning-agree / planning-disagree / planning-different | Metacognitive.planning |
| monitoring-* | Metacognitive.monitoring |
| evaluating-* | Metacognitive.evaluating |
| coordinate\procedure-* | Coordinative.coordinate_procedures |
| coordinate\participants-* | Coordinative.coordinate_participants |
| emotional\expression | Socio-emotional.emotional_expression |
| encouragement | Socio-emotional.encouragement |
| selfdisclosure / self_disclosure | Socio-emotional.self_disclosure |

---

## Accuracy checklist (all agents)

Before finalizing or challenging a label, verify:

- [ ] **Tier1** matches the *primary* intent: content vs process vs coordination vs socio-emotional.
- [ ] **Tier2** is correct for that tier1 (e.g. concept_exploration vs solution_development; planning vs monitoring vs evaluating).
- [ ] The label exists in **label-taxonomy.csv** (exact string, including capitalization).
- [ ] At least one evidence span explicitly supports this label (no unsupported inference).

---

## Reference

- **Taxonomy (valid codes):** **cloudbot/data/label-taxonomy.csv**
- **Training (auxiliary):** **cloudbot/data/training/**
- **Pipeline:** **cloudbot/FLOW.md**
