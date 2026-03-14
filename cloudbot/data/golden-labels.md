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

### Tier 1 boundaries (when to use which tier)

| Tier 1 | Meaning | When to use |
|--------|---------|-------------|
| **Cognitive** | **Task content:** what is being discussed, learned, or solved (concepts, facts, solutions). | Utterance is about *what* the task domain is—concepts, definitions, solution content, correctness of answers about the task. |
| **Metacognitive** | **Process:** how we approach, plan, monitor, or evaluate the task. | Utterance is about *how* to do the task—planning steps, checking progress, evaluating whether the approach or output is OK, time/pace. |
| **Coordinative** | **Coordination:** who does what, how we work together (roles, procedures, logistics). | Utterance is about task allocation, roles, procedures, tools, or logistics (e.g. "who should do what?", "how should we share the doc?"). |
| **Socio-emotional** | **Relationship/affect:** emotions, encouragement, self-disclosure, group belonging. | Utterance expresses emotion, support, encouragement, self-disclosure, or sense of group. |

**Critical distinctions:**

- **Cognitive vs Metacognitive:** If the utterance is about *the content* of the task (e.g. "what is Bloom's taxonomy?", "this answer is correct") → **Cognitive**. If it is about *how we’re doing the task* (e.g. "should we move on?", "are we on track?") → **Metacognitive**.
- **Coordinative vs Socio-emotional:** If it’s about *task structure* (who does what, how we run the session) → **Coordinative**. If it’s about *feelings, support, or belonging* → **Socio-emotional**.

### Tier 2 (sub-category) — precise use

- **Cognitive:** `concept_exploration` = learning/clarifying concepts; `solution_development` = developing or refining solutions (e.g. naming, defining, analyzing in service of the task solution).
- **Metacognitive:** `planning` = how to approach/solve; `monitoring` = progress, pace, whether we’re on track; `evaluating` = judging solutions/outputs.
- **Coordinative:** `coordinate_participants` = roles, who does what; `coordinate_procedures` = logistics, tools, how we share/work.
- **Socio-emotional:** `emotional`, `encouragement`, `forming_sense_of` — use taxonomy descriptions and examples in **label-taxonomy.csv**.

### Tier 3 (action) — precise use

- **ask** = question (information or procedure).
- **answer** = direct answer to a question.
- **agree** = agreement without adding new explanation (e.g. "yeah", "I think so").
- **disagree** = disagreement.
- **give** = providing information or input without being in response to a question.
- **build_on** = agreeing **and** extending with explanation or new content.

**Important:** Use **build_on** only when there is clear extension/elaboration. Simple agreement → **agree**, not build_on.

### Label format

- **Canonical form:** `Tier1.tier2.tier3` (e.g. `Cognitive.concept_exploration.ask`, `Metacognitive.monitoring.ask`).
- **Socio-emotional:** Some entries have only tier2 (e.g. `Socio-emotional.emotional`); use as in **label-taxonomy.csv** (no tier3).
- **From training CSV:** Human coders may use shorthand. Map to canonical form using **label-taxonomy.csv** as the single list of valid codes (see mapping table below).

---

## Decision rules (for accuracy)

Apply in order when in doubt:

1. **Tier1 first:** Is it about *content* (what) → Cognitive. *Process* (how we do it) → Metacognitive. *Who does what / logistics* → Coordinative. *Feelings, support, belonging* → Socio-emotional.
2. **Cognitive tier2:** About *concepts/definitions/learning* → concept_exploration. About *solutions, answers, task product* (naming, defining, analyzing the solution) → solution_development.
3. **Metacognitive tier2:** About *how to approach or plan* → planning. About *progress, pace, on track* → monitoring. About *judging quality of solution/output* → evaluating.
4. **Tier3:** Is there a *question*? → ask. A *direct answer to a question*? → answer. *Agreement with no new content*? → agree. *Agreement plus new explanation or extension*? → build_on. *New information not in response to a question*? → give. *Disagreement*? → disagree.

---

## Edge cases and examples

| Utterance / situation | Correct direction | Common error to avoid |
|------------------------|-------------------|------------------------|
| "what is Bloom's taxonomy?" | Cognitive.concept_exploration.ask (content) | Not Metacognitive.planning.ask |
| "how should we solve this question?" | Metacognitive.planning.ask (process) | Not Cognitive |
| "should we move on to number two?" / "are we on the right track?" | Metacognitive.monitoring.ask | Not Cognitive; about progress |
| "yeah" / "I think so" / "mhm, yeah" (no new content) | agree (in same tier2 as prior turn) | Not build_on; not give |
| "that makes sense, then we didn't need to google" (agrees + extends) | build_on | agree = no extension |
| "then you can start" (handing off / procedure) | Coordinative.coordinate_procedures.give | Not Socio-emotional |
| "I was TA at this course, I have experience" (personal disclosure) | Socio-emotional (e.g. forming_sense_of or emotional per taxonomy) | Not Cognitive |
| "do you think this solution is ok?" | Metacognitive.evaluating.ask | Not Cognitive (evaluating the *output*, not teaching content) |
| "word count" / "how much is it?" (about the product) | Metacognitive.evaluating.give / evaluating.ask | About judging output |
| "we can bullet point first then form it" (how to do the task) | Metacognitive.planning.give | Not solution_development (that’s content of solution) |

---

## Training shorthand → canonical mapping

Use **label-taxonomy.csv** for the exact list. Common mappings:

| Training shorthand | Canonical (example) |
|--------------------|---------------------|
| concept\exploration-ask | Cognitive.concept_exploration.ask |
| concept\exploration-answer, give, agree, disagree, build_on | Cognitive.concept_exploration.* |
| solution\development-* | Cognitive.solution_development.* |
| monitoring-ask, monitoring-answer, etc. | Metacognitive.monitoring.* |
| planning-* | Metacognitive.planning.* |
| evaluating-* | Metacognitive.evaluating.* |
| coordinate\procedure-* | Coordinative.coordinate_procedures.* (exact: coordinate_procedures in taxonomy) |
| coordinate\participants-* | Coordinative.coordinate_participants.* |
| emotional\expression | Socio-emotional.emotional (tier2 only in taxonomy) |
| selfdisclosure | Socio-emotional.forming_sense_of or Socio-emotional.emotional (use taxonomy; no "selfdisclosure" code—map to closest: forming sense of group / personal disclosure) |
| encouragement | Socio-emotional.encouragement |

---

## Accuracy checklist (all agents)

Before finalizing or challenging a label, verify:

- [ ] **Tier1** matches the *primary* intent: content vs process vs coordination vs socio-emotional.
- [ ] **Tier2** is correct for that tier1 (e.g. concept_exploration vs solution_development; planning vs monitoring vs evaluating).
- [ ] **Tier3** matches the speech act: ask vs answer vs give vs agree vs build_on vs disagree (build_on only if there is clear extension).
- [ ] The label exists in **label-taxonomy.csv** (exact string, including capitalization).
- [ ] At least one evidence span explicitly supports this label (no unsupported inference).

---

## Reference

- **Taxonomy (valid codes):** **cloudbot/data/label-taxonomy.csv**
- **Training (auxiliary):** **cloudbot/data/training/**
- **Pipeline:** **cloudbot/FLOW.md**
