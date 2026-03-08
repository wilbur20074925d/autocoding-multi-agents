# Testing data

Put testing prompts here (e.g. JSONL or CSV). You then send them through chat to the AI for final evaluation.

Example: add a file `prompts.jsonl` with one prompt per line:
```json
{"prompt": "do you think this solution is ok?"}
```

In chat, ask the AI to run the full autocoding pipeline on each and output the final label so you can evaluate.
