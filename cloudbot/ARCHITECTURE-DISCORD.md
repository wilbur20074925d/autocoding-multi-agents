# 1 Orchestrator + 4 Display Bots (Discord)

This document describes the **Discord-facing architecture**: one main controller that receives user messages and a **response dispatcher** that sends different parts of the pipeline reasoning to **four Discord bot identities**, so each agent appears as a separate bot in the thread.

## Principle

- **Backend unchanged**: Agents, skills, taxonomy, and workflows (e.g. `workflows/autocoding.yaml`) continue to run inside the **OpenClaw runtime**. No change to pipeline logic.
- **New layer only**: A **Discord response dispatcher** that takes the **structured output** from the pipeline and produces **four Discord-ready messages**, one per role, for the main controller to post with four different bot tokens.

## Architecture

```
User
  → Main Controller (receives Discord message)
  → OpenClaw workflow (signal_extractor → label_coder → boundary_critic → adjudicator)
  → structured output
  → Dispatcher (cloudbot/discord/dispatcher.py)
  → 4 Discord bots post sequential messages
```

## Roles (Display Bots)

| Order | Role             | Bot identity        | Content posted |
|-------|------------------|---------------------|----------------|
| 1     | **Signal Extractor** | Bot 1 (e.g. token A) | Evidence spans, candidate signals, ambiguity |
| 2     | **Label Coder**      | Bot 2 (e.g. token B) | Draft/revised labels, evidence used, revision note |
| 3     | **Boundary Critic**  | Bot 3 (e.g. token C) | Challenges, request missing evidence |
| 4     | **Adjudicator**      | Bot 4 (e.g. token D) | Final labels, confidence, uncertain, optional retry |

## Pipeline Output Contract

The OpenClaw runtime must return a single **structured output** dict so the dispatcher can slice by role. Expected shape (aligned with `workflows/autocoding.yaml`):

```python
{
    "prompt": "user prompt text",           # optional, for first message
    "context": { ... },                     # optional metadata
    "signal_extractor": {
        "evidence_spans": [...],
        "candidate_signals": [...],
        "ambiguity": [...]
    },
    "label_coder": {
        "labels": [...],
        "uncertain": [...],
        "revision_note": "..."
    },
    "boundary_critic": {
        "challenges": [...],
        "request_missing_evidence": [...]
    },
    "adjudicator": {
        "final_labels": [...],
        "uncertain": [...],
        "retry": null | { "target": "...", "instruction": "..." }
    }
}
```

Types are defined in **`cloudbot/discord/pipeline_output.py`** for reference.

## Using the Dispatcher

From the main controller (after you have `pipeline_output` from OpenClaw):

```python
from cloudbot.discord import prepare_four_bot_messages_split, DISPLAY_BOT_ORDER

# pipeline_output = result from OpenClaw autocoding workflow
# Prefer split when prompts or evidence spans are long (Discord 2000-char limit per message).
messages = prepare_four_bot_messages_split(pipeline_output, include_prompt_in_first=True)

# messages is list of (role_id, list[str]): four roles in order; each role may have multiple chunks
for role_id, chunks in messages:
    for content in chunks:
        await send_as_bot(role_id, channel_id, content)
```

- **`prepare_four_bot_messages`**: Returns `list[(role_id, str)]` — one message per bot (may truncate at ~1900 chars if content is huge).
- **`prepare_four_bot_messages_split`**: Same pipeline formatting but each role gets `list[str]` chunks (~1900 chars); **use this in production** so long prompts and full evidence-span text are not cut off. Post chunks in order for each role.

Role ids: `signal_extractor`, `label_coder`, `boundary_critic`, `adjudicator` (constants in `cloudbot.discord.dispatcher`).

## Main Controller Responsibilities

1. **Receive**: Handle incoming Discord message (e.g. single prompt or trigger to run on a CSV).
2. **Invoke OpenClaw**: Run the autocoding workflow with the prompt (and optional context). Backend logic stays in OpenClaw; the controller only passes input and receives the structured output above.
3. **Dispatch**: Call `prepare_four_bot_messages_split(pipeline_output)` (or `prepare_four_bot_messages` for short outputs only).
4. **Post**: For each `(role_id, chunks)`, send every chunk in order with the Discord client/token for that role.

## Four Bot Identities

You need four Discord **applications/bots** (or four tokens if your setup uses one app with multiple bot users). Each token is mapped to a role:

- Token A → `signal_extractor`
- Token B → `label_coder`
- Token C → `boundary_critic`
- Token D → `adjudicator`

The dispatcher does not send to Discord; it only produces the message text. Your controller holds the four clients (or one client that can switch token per post) and performs the actual `channel.send()` or equivalent.

## Files

| File | Purpose |
|------|---------|
| `cloudbot/discord/dispatcher.py` | Builds 4 messages from pipeline output; formatting per role |
| `cloudbot/discord/pipeline_output.py` | TypedDict contract for pipeline output |
| `cloudbot/discord/format.py` | Discord formatting (tables, sections, truncation) used by dispatcher |
| `cloudbot/INTEGRATIONS.md` | Discord formatting and Google Sheets; reference for full pipeline + integrations |

## Optional: Google Sheets

After the pipeline completes (and after you post the 4 bot messages), you can still append the result to the Google Sheet using `cloudbot.integrations.sheets.append_result(...)` with `adjudicator_output["final_labels"]` and `adjudicator_output.get("uncertain")`, as in **INTEGRATIONS.md**.
