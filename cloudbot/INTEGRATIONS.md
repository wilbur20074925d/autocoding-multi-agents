# Integrations: Discord and Google Sheets

## Discord: 1 orchestrator + 4 display bots

For the **multi-agent display** architecture (one main controller, four Discord bot identities posting sequential messages), see **ARCHITECTURE-DISCORD.md**. The **response dispatcher** in `cloudbot/discord/dispatcher.py` turns pipeline output into four messages (Signal Extractor, Label Coder, Boundary Critic, Adjudicator). The main controller receives the Discord message, runs the OpenClaw workflow, then calls `prepare_four_bot_messages(pipeline_output)` and posts each message with the corresponding bot token.

## Discord: structured messages (single bot)

When sending autocoding output to Discord (single bot or legacy), format content so it looks clear and readable (tables, sections, code blocks). Use the **Discord formatter** in `cloudbot/discord/format.py`.

- **Tables** → Use code blocks with aligned columns (Discord does not render markdown tables).  
  `table_from_rows()`, `table_from_dicts()`
- **Sections** → Bold title + body: `section("Final labels", ...)`
- **Structured output / JSON** → `json_block(obj)` or `code_block(text, "json")`
- **Bullet lists** → `bullet_list(items, header="Uncertain")`
- **One full result** → `pipeline_result_discord(prompt, final_labels, uncertain=...)`
- **Long text** → Discord limit is 2000 characters; use `split_messages(text)` or `_truncate()`.

Example (pseudo-code when posting to Discord):

```python
from cloudbot.discord.format import pipeline_result_discord, section, table_from_rows

# After Adjudicator for one prompt:
msg = pipeline_result_discord(
    prompt=prompt,
    final_labels=adjudicator_output["final_labels"],
    uncertain=adjudicator_output.get("uncertain"),
)
# Send msg to Discord (e.g. channel.send(msg))
```

Use different styles for different content: tables for label lists, code blocks for raw JSON, bold sections for headings, so Discord messages are well structured and easy to read.

---

## Google Sheets: update after each prompt

After **each prompt** is fully processed (Signal Extractor → Label Coder → Boundary Critic → revision → Adjudicator), append one row to the Multi-Agent Autocoding Google Sheet:

**Sheet:** [Multi-Agent Autocoding](https://docs.google.com/spreadsheets/d/1atmf7D_qXQzEUVmx82TFv9ztyzkPmG1FSYcFPIyF6rc/edit?usp=sharing)

### Setup

1. **Google Cloud Console**: Enable **Google Sheets API** and **Google Drive API**.
2. **Service account**: Create a service account, download its JSON key.
3. **Share the spreadsheet**: Share the sheet with the service account email (e.g. `xxx@xxx.iam.gserviceaccount.com`) as **Editor**.
4. **Credentials** (one of):
   - Put the JSON key at `~/.config/gspread/service_account.json`, or
   - Set `GOOGLE_APPLICATION_CREDENTIALS` to the path of the JSON key, or
   - Pass `credentials_path="path/to/key.json"` when calling the integration.

Install: `pip install gspread`

### Usage

```python
from cloudbot.integrations.sheets import append_result, ensure_header_row, DEFAULT_SHEET_ID

# Optional: once at start of a batch, ensure header row exists
ensure_header_row()  # Writes "Prompt" | "Final labels" | "Uncertain" | "Timestamp" | "Row index"

# After each prompt is completed (Adjudicator output available):
append_result(
    prompt=user_prompt,
    final_labels=adjudicator_output["final_labels"],
    uncertain=adjudicator_output.get("uncertain"),
    row_index=index_in_batch,  # optional, 1-based
)
```

Columns appended: **Prompt** | **Final labels** | **Uncertain** | **Timestamp** | **Row index** (if provided).

To use a different sheet, set env `AUTOCODING_SHEET_ID` or pass `sheet_id="..."` to `append_result()` and `ensure_header_row()`.

---

## Pipeline flow with integrations

1. User sends a **single prompt** or a **CSV of prompts**.
2. For each prompt, run the full pipeline (Signal Extractor → … → Adjudicator).
3. **After each prompt completes:**
   - Format the result for Discord with `cloudbot.discord.format` and send the message (tables, sections, code blocks for a nice layout).
   - Append the result to the Google Sheet with `cloudbot.integrations.sheets.append_result(...)`.
4. Only then start the next prompt (sequential processing for CSV).
