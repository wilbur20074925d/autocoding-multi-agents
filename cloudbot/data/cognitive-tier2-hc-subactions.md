# Human coder sub-actions: Cognitive tier2 (parallel structure)

Human labels often use **`strand\subaction`** or **`strand/subaction`** (e.g. `solution\development-ask`, `concept\exploration-give`).

**Critical:** The **same sub-action name** (ask, answer, agree, disagree, give, build on) appears in **both** strands. The **strand prefix** encodes the semantic focus:

- **`solution\development-*`** → canonical **`Cognitive.solution_development`**
- **`concept\exploration-*`** → canonical **`Cognitive.concept_exploration`**

Do **not** infer tier2 from the sub-action alone (e.g. “agree” is **not** enough—you need **solution** vs **concept** focus).

---

## Solution development — `solution\development-*`

**Focus:** developing **solutions for** the learning task (task products, answers, options, how to complete or justify the response).

| Sub-action | Meaning |
|------------|---------|
| **ask** | Ask questions **for developing solutions** |
| **answer** | Answer questions **for developing solutions** |
| **agree** | Agree **with an answer** (solution-oriented) |
| **disagree** | Disagree **with an answer** |
| **give** | Provide information **without being asked**, for **solution development** |
| **build on** | Agree with **explanation** (in the solution strand) |

---

## Concept exploration — `concept\exploration-*`

**Focus:** learning or clarifying **concepts of** the learning task (meanings, definitions, theory—not the task product itself).

| Sub-action | Meaning |
|------------|---------|
| **ask** | Asking questions **for learning or clarifying concepts** |
| **answer** | Answering questions **for learning or clarifying concepts** |
| **agree** | Agree **with no explanation** (concept strand; still check referent—e.g. agreeing on a conceptual point vs choosing option C for the task) |
| **disagree** | Disagree **with an answer** (conceptual stance) |
| **give** | Provide information **without asking**, for **conceptual** clarification |
| **build on** | Agree with **explanation** (concept strand) |

---

## Mapping to pipeline

- CSV / Discord **HC1**, **HC2** (or `hc1` / `hc2`) are passed in `context`.
- The pipeline detects:
  - `solution` + `development` in the value → bias / repair toward **`Cognitive.solution_development`**
  - `concept` + `exploration` in the value → bias / repair toward **`Cognitive.concept_exploration`**
- Final autocoding labels remain **`Tier1.tier2`** only (no tier3 in output); sub-actions inform disambiguation and evaluation against human coding.

See also **golden-labels.md** (Cognitive tier2) and **label-taxonomy.csv**.
