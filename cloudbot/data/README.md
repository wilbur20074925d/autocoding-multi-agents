# Data and Evaluation

## Overview

- **Training data**: User prompts with **correct labels** (ground truth). Use these to calibrate or validate the autocoding pipeline.
- **Testing data**: Prompts you send through **chat with the AI** for final evaluation. The AI runs the pipeline (Signal Extractor → Label Coder → Boundary Critic → Adjudicator) and you compare outputs to your held-out labels or judge quality.

## Folder structure

| Folder       | Purpose |
|-------------|---------|
| `training/` | Labeled prompts (user prompt + right label). Your gold standard. |
| `testing/`  | Optional: store testing prompts here; you send them in chat for evaluation. |

## Training data format

Place training examples in `training/`. Two formats are supported.

### Option 1: CSV (recommended for human-coded data)

Use a CSV with the following columns. **HC1** and **HC2** are human-coder labels and are the **correct answers** (ground truth) for training and evaluation.

| Column | Description |
|--------|-------------|
| `group` | Group or session ID (e.g. G11) |
| `timestamp-mm` | Timestamp (e.g. 00:01:13) |
| `people` | Participants (e.g. 1,2,3) |
| `context` | Context tag (e.g. no-gai) |
| `sentence` | The utterance to be labeled → used as the **prompt** |
| `LLMs-revised sentence` | Optional revised version of the sentence |
| **HC1** | Human Coder 1 labels (comma-separated if multiple) |
| **HC2** | Human Coder 2 labels (comma-separated if multiple) |

Example:

```csv
group,timestamp-mm,people,context,sentence,LLMs-revised sentence,HC1,HC2
G11,00:01:13,"1,2,3",no-gai,hhhh,hhh,"cognitive, metacognitive","socio-emotional, metacognitive"
```

Labels in HC1 and HC2 must follow the 3-tier taxonomy in **label-taxonomy.csv** (in this folder: `cloudbot/data/label-taxonomy.csv`) (e.g. `Cognitive.concept_exploration.ask`, or tier1-only like `cognitive`, `metacognitive`). Use the script in `training/load_training_csv.py` to load this CSV and convert to a unified format for evaluation.

**Context for the pipeline:** When you run the autocoding pipeline (Signal Extractor → Label Coder → Boundary Critic → Adjudicator), pass **group**, **timestamp-mm**, **people**, and **context** along with each prompt. All four agents are instructed to take these into consideration (e.g. who is speaking, which session, when in the session) when extracting evidence, assigning labels, challenging boundaries, and making final decisions. See **FLOW.md** and each agent’s skill under `.cursor/skills/`.

### Option 2: JSONL (simple prompt + single label)

One JSON object per line:

```json
{"prompt": "what is bloom taxonomy?", "label": "Cognitive.concept_exploration.ask"}
{"prompt": "how should we solve this question?", "label": "Metacognitive.planning.ask"}
```

Or CSV with columns `prompt` and `label` (and optionally `id`, `notes`):

```csv
prompt,label
what is bloom taxonomy?,Cognitive.concept_exploration.ask
how should we solve this question?,Metacognitive.planning.ask
```

Labels must follow the 3-tier taxonomy in **label-taxonomy.csv** (in this folder) (e.g. `Cognitive.concept_exploration.ask`).

### Prompts-only CSV (no labels)

For a CSV that contains **only** user prompts (one per row), use **`cloudbot/data/load_prompts_csv.py`** to load prompts. The script supports a header column named `prompt` or `sentence`, or no header (first column = prompt). When the user sends a prompts-only CSV, the AI must process **one prompt at a time**: run the full pipeline for the first prompt and **complete** it before starting the next. No overlapping or batched runs.

Example CSV:

```csv
prompt
Do you all know about Bloom's Taxonomy?
No, this is the first time I'm hearing about it.
We can Google it, right?
```

## Final evaluation (testing via chat)

1. Take testing prompts (from `testing/` or elsewhere).
2. In chat, send each prompt (or a batch) and ask the AI to run the **autocoding pipeline**:
   - "Run the Signal Extractor on this prompt, then Label Coder, then Boundary Critic, then Adjudicator."
3. Use the Adjudicator’s final label as the **model prediction**.
4. Compare to your **held-out correct labels** (if you have them) to compute accuracy, or review quality manually.

You can say for example:

- "Here is a testing prompt: [paste]. Run the full autocoding pipeline and give me the final label."
- "I have testing data in `cloudbot/data/testing/prompts.jsonl`. Process each and output final labels so I can evaluate."
- "Attached is a CSV of prompts. Run the pipeline on each row one by one; complete each before starting the next."

## Quick reference

- Label taxonomy: **cloudbot/data/label-taxonomy.csv** (or `label-taxonomy.csv` in this folder)
- Pipeline order: **FLOW.md**
- Skills: `.cursor/skills/signal-extractor/`, `label-coder/`, `boundary-critic/`, `adjudicator/`
