"""
Discord runner: 1 Controller bot + 4 display bots (Signal, Label, Critic, Judge).

Flow:
  Controller receives "Label this prompt..." → run pipeline → send_as_bot(role)
    → SignalBot.send() | LabelBot.send() | CriticBot.send() | JudgeBot.send()

If you set 5 bot tokens, each role sends with its own bot. If only the Controller
token is set, messages are sent via a channel webhook so they appear as four
different roles (Signal Extractor, Label Coder, Boundary Critic, Adjudicator).
Webhook fallback requires the bot to have "Manage Webhooks" in the channel.

Requires discord.py and at least:
  DISCORD_CONTROLLER_TOKEN (or CONTROLLER_TOKEN)
Optional (for separate bot identities per role):
  DISCORD_SIGNAL_BOT_TOKEN, DISCORD_LABEL_BOT_TOKEN,
  DISCORD_CRITIC_BOT_TOKEN, DISCORD_JUDGE_BOT_TOKEN.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any

import discord
from discord import Message

try:
    from discord import Webhook
except ImportError:
    Webhook = None  # type: ignore[misc, assignment]

from .dispatcher import (
    ADJUDICATOR,
    BOUNDARY_CRITIC,
    LABEL_CODER,
    SIGNAL_EXTRACTOR,
    prepare_four_bot_messages,
)
from .format import format_prompt_received

# Trigger phrase (case-insensitive)
LABEL_PROMPT_TRIGGER = "label this prompt"

# Training/reflection trigger: user uploads training.csv and asks to learn/refine.
TRAINING_TRIGGER_PHRASES = (
    "update training csv",
    "train on this csv",
    "learn from this csv",
    "run reflection",
    "refine skills",
)

# Testing/evaluation trigger: user uploads testing.csv and asks to evaluate accuracy.
TESTING_TRIGGER_PHRASES = (
    "test this csv",
    "testing csv",
    "evaluate this csv",
    "run testing",
    "evaluate accuracy",
)

# Discord mention patterns: user <@ID> / <@!ID>, role <@&ID>
_USER_MENTION_PATTERN = re.compile(r"<@!?\d+>")
_ROLE_MENTION_PATTERN = re.compile(r"<@&\d+>")


def _strip_mentions_for_prompt(content: str, message: Message) -> str:
    """Remove Discord user and role mentions from message content so the pipeline gets clean text."""
    text = (content or "").strip()
    # Replace user mentions with @display_name when we can resolve them
    mention_ids = {int(m) for m in re.findall(r"<@!?(\d+)>", text)}
    for user in getattr(message, "mentions", []):
        if user.id in mention_ids:
            text = re.sub(rf"<@!?{user.id}>", f"@{user.display_name}", text)
    # Remove any remaining user mentions (e.g. user left server) and all role mentions <@&id>
    text = _USER_MENTION_PATTERN.sub("", text)
    text = _ROLE_MENTION_PATTERN.sub("", text)
    return " ".join(text.split()).strip()

# Display names when using webhook fallback (one bot, four visible roles)
ROLE_DISPLAY_NAMES = {
    SIGNAL_EXTRACTOR: "Signal Extractor",
    LABEL_CODER: "Label Coder",
    BOUNDARY_CRITIC: "Boundary Critic",
    ADJUDICATOR: "Adjudicator",
}

# One webhook per channel for webhook fallback (channel_id -> Webhook)
_channel_webhook_cache: dict[int, Any] = {}
WEBHOOK_NAME = "Autocoding"


def _get_tokens() -> dict[str, str]:
    """Read tokens from env. Each role uses its own bot client.

    Backwards-compatible env var names:
    - Controller: DISCORD_CONTROLLER_TOKEN, CONTROLLER_TOKEN, DISCORD_CONTROLLER_BOT_TOKEN
    """
    return {
        "controller": (
            os.environ.get("DISCORD_CONTROLLER_TOKEN")
            or os.environ.get("CONTROLLER_TOKEN")
            or os.environ.get("DISCORD_CONTROLLER_BOT_TOKEN", "")
        ).strip(),
        SIGNAL_EXTRACTOR: os.environ.get("DISCORD_SIGNAL_BOT_TOKEN", "").strip(),
        LABEL_CODER: os.environ.get("DISCORD_LABEL_BOT_TOKEN", "").strip(),
        BOUNDARY_CRITIC: os.environ.get("DISCORD_CRITIC_BOT_TOKEN", "").strip(),
        ADJUDICATOR: os.environ.get("DISCORD_JUDGE_BOT_TOKEN", "").strip(),
    }


async def get_or_create_pipeline_webhook(channel: discord.abc.MessageableChannel) -> Any:
    """Get or create a webhook for this channel so we can send as different role names. Returns None if not possible."""
    if not hasattr(channel, "id"):
        return None
    cid = int(channel.id)
    if cid in _channel_webhook_cache:
        return _channel_webhook_cache[cid]
    try:
        if hasattr(channel, "webhooks"):
            webhooks = await channel.webhooks()
            for wh in webhooks:
                if wh.name == WEBHOOK_NAME:
                    _channel_webhook_cache[cid] = wh
                    return wh
        if hasattr(channel, "create_webhook"):
            wh = await channel.create_webhook(name=WEBHOOK_NAME)
            _channel_webhook_cache[cid] = wh
            return wh
    except discord.Forbidden:
        print("Warning: bot needs 'Manage Webhooks' to post as four roles with one token.")
    except Exception as e:
        print(f"Warning: could not get/create webhook: {e}")
    return None


class DisplayBot(discord.Client):
    """Display bot that only sends when told to (no on_message)."""

    def __init__(self, role_id: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.role_id = role_id

    async def send_to_channel(self, channel_id: int, content: str) -> None:
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        await channel.send(content)

    async def on_ready(self) -> None:
        print(f"  {self.role_id} bot logged in as {self.user}")


class ControllerBot(discord.Client):
    """
    Controller bot: listens for 'Label this prompt...', runs pipeline,
    then dispatches to SignalBot, LabelBot, CriticBot, JudgeBot.
    """

    def __init__(
        self,
        role_to_bot: dict[str, DisplayBot],
        run_pipeline: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.role_to_bot = role_to_bot
        self.run_pipeline = run_pipeline

    async def on_ready(self) -> None:
        print(f"Controller bot logged in as {self.user}")

    async def on_message(self, message: Message) -> None:
        if message.author.bot:
            return
        content = (message.content or "").strip()
        lower = content.lower()

        # --- Training / reflection mode (CSV attachment) ---
        if any(p in lower for p in TRAINING_TRIGGER_PHRASES):
            await self._handle_training_csv(message)
            return

        # --- Testing / evaluation mode (CSV attachment) ---
        if any(p in lower for p in TESTING_TRIGGER_PHRASES):
            await self._handle_testing_csv(message)
            return

        # --- Normal labeling mode ---
        if LABEL_PROMPT_TRIGGER not in lower:
            return

        prompt = _strip_mentions_for_prompt(content, message)
        channel_id = message.channel.id

        # Display the prompt received first (from Controller)
        await message.channel.send(format_prompt_received(prompt))

        # Run pipeline (sync); run in executor to avoid blocking
        loop = asyncio.get_event_loop()
        pipeline_output = await loop.run_in_executor(
            None,
            lambda: self.run_pipeline(prompt, None),
        )
        if "prompt" not in pipeline_output:
            pipeline_output["prompt"] = prompt

        messages = prepare_four_bot_messages(
            pipeline_output,
            include_prompt_in_first=True,
        )

        # Each role sends with its own bot client, or via webhook so they appear as four different senders
        for role_id, text in messages:
            bot = self.role_to_bot.get(role_id)
            if bot is not None:
                await bot.send_to_channel(channel_id, text)
            else:
                # No separate bot for this role: send via webhook with role display name (four visible roles, one token)
                webhook = await get_or_create_pipeline_webhook(message.channel)
                display_name = ROLE_DISPLAY_NAMES.get(role_id, role_id)
                if webhook is not None:
                    await webhook.send(content=text, username=display_name)
                else:
                    await message.channel.send(f"**[{display_name}]**\n{text}")

    async def _handle_training_csv(self, message: Message) -> None:
        """
        Accept a CSV attachment, overwrite cloudbot/data/training/training.csv,
        then process line-by-line and finally generate suggested update files.
        """
        attachments = list(getattr(message, "attachments", []) or [])
        csv_attachments = [a for a in attachments if (a.filename or "").lower().endswith(".csv")]
        if not csv_attachments:
            await message.channel.send(
                "No CSV attachment found. Please upload a `.csv` (e.g. `training.csv`) and send a message like "
                "`update training csv`."
            )
            return

        att = csv_attachments[0]
        try:
            data = await att.read()
        except Exception as e:
            await message.channel.send(f"Failed to read attachment: {e}")
            return

        # Save uploaded CSV as the canonical training file
        try:
            from pathlib import Path

            repo_root = Path(__file__).resolve().parents[2]
            training_path = repo_root / "cloudbot" / "data" / "training" / "training.csv"
            training_path.parent.mkdir(parents=True, exist_ok=True)
            training_path.write_bytes(data)
        except Exception as e:
            await message.channel.send(f"Failed to write training.csv on server: {e}")
            return

        await message.channel.send(
            f"Training CSV saved as `{training_path}`. Processing row-by-row and generating refinement suggestions…"
        )

        # Run evaluation + reflection and stream outcomes.
        try:
            from cloudbot.data.training.load_training_csv import load_training_csv
            from cloudbot.eval.driver import (
                extract_predicted_label,
            )
            from cloudbot.eval.compare import compare_one
            from cloudbot.eval.normalize import normalize_human_labels
            from cloudbot.eval.reflection import reflect_mismatch
            from cloudbot.eval.taxonomy import build_tier2_to_tier1, load_taxonomy_rows
            from cloudbot.pipeline.run_pipeline import run_autocoding_pipeline

            taxonomy_path = repo_root / "cloudbot" / "data" / "label-taxonomy.csv"
            taxonomy_rows = load_taxonomy_rows(taxonomy_path)
            tier2_to_tier1 = build_tier2_to_tier1(taxonomy_rows)

            examples = load_training_csv(training_path)
        except Exception as e:
            await message.channel.send(f"Failed to load training/taxonomy: {e}")
            return

        mismatches = []
        normalization_warnings: list[str] = []
        processed = 0
        matched = 0

        # Post results row-by-row (learning outcome + refinement)
        for ex in examples:
            prompt = (ex.get("prompt") or "").strip()
            if not prompt:
                continue
            processed += 1

            hc1 = ex.get("hc1") or []
            hc2 = ex.get("hc2") or []
            p1, w1 = normalize_human_labels(list(hc1), tier2_to_tier1=tier2_to_tier1)
            p2, w2 = normalize_human_labels(list(hc2), tier2_to_tier1=tier2_to_tier1)
            normalization_warnings.extend(w1 + w2)

            # golden = union of hc1+hc2 patterns
            seen = set()
            golden_patterns = []
            for p in (p1 + p2):
                k = (p.tier1, p.tier2, p.tier3)
                if k in seen:
                    continue
                seen.add(k)
                golden_patterns.append(p)
            if not golden_patterns:
                continue

            pipeline_out = run_autocoding_pipeline(
                prompt,
                context={k: ex.get(k) for k in ("group", "timestamp-mm", "people", "context") if ex.get(k)},
            )
            predicted = extract_predicted_label(pipeline_out)
            comp = compare_one(predicted, golden_patterns)
            if comp.is_match:
                matched += 1
                await message.channel.send(
                    f"**Row {processed}** ✅ match\n"
                    f"- Prompt: `{prompt[:180] + ('…' if len(prompt) > 180 else '')}`\n"
                    f"- Predicted: `{predicted}`"
                )
                continue

            item = reflect_mismatch(
                prompt=prompt,
                comparison=comp,
                context_metadata={k: ex.get(k) for k in ("group", "timestamp-mm", "people", "context") if ex.get(k)},
            )
            mismatches.append(item)

            # “learning outcome” = mismatch summary + which role files to refine
            targets = ", ".join(f"`{t}`" for t in item.target_skill_files)
            await message.channel.send(
                f"**Row {processed}** ❌ mismatch (`{item.mismatch_type}`)\n"
                f"- Prompt: `{prompt[:180] + ('…' if len(prompt) > 180 else '')}`\n"
                f"- Predicted: `{item.predicted}`\n"
                f"- Golden: `{', '.join(item.golden)}`\n"
                f"- Reason: {item.reason}\n"
                f"- Refinement targets: {targets}"
            )

        # Write artifacts + upload
        try:
            from cloudbot.eval.driver import write_outputs

            out_md = repo_root / "suggested_skill_updates.md"
            out_jsonl = repo_root / "reflection_log.jsonl"
            out_warn = repo_root / "normalization_warnings.txt"
            write_outputs(
                repo_root=repo_root,
                mismatches=mismatches,
                normalization_warnings=sorted(set(normalization_warnings)),
                out_md=out_md,
                out_jsonl=out_jsonl,
                out_warnings=out_warn,
            )
        except Exception as e:
            await message.channel.send(f"Failed to write output files: {e}")
            return

        await message.channel.send(
            f"Done. Processed **{processed}** rows: **{matched}** match, **{len(mismatches)}** mismatch.\n"
            "Uploading `suggested_skill_updates.md` and `normalization_warnings.txt`…"
        )

        try:
            await message.channel.send(file=discord.File(str(out_md)))
            await message.channel.send(file=discord.File(str(out_warn)))
        except Exception as e:
            await message.channel.send(f"Could not upload files (check bot permissions): {e}")

    async def _handle_testing_csv(self, message: Message) -> None:
        """
        Accept a CSV attachment (testing set), evaluate row-by-row against HC1/HC2,
        and report whether the final prediction matches any human label.
        """
        attachments = list(getattr(message, "attachments", []) or [])
        csv_attachments = [a for a in attachments if (a.filename or "").lower().endswith(".csv")]
        if not csv_attachments:
            await message.channel.send(
                "No CSV attachment found. Please upload a `.csv` (e.g. `testing.csv`) and send a message like "
                "`test this csv`."
            )
            return

        att = csv_attachments[0]
        try:
            data = await att.read()
        except Exception as e:
            await message.channel.send(f"Failed to read attachment: {e}")
            return

        # Save uploaded CSV as a testing file (optional, for auditing)
        try:
            from pathlib import Path

            repo_root = Path(__file__).resolve().parents[2]
            testing_path = repo_root / "cloudbot" / "data" / "testing" / "testing.csv"
            testing_path.parent.mkdir(parents=True, exist_ok=True)
            testing_path.write_bytes(data)
        except Exception as e:
            await message.channel.send(f"Failed to write testing.csv on server: {e}")
            return

        await message.channel.send(
            f"Testing CSV saved as `{testing_path}`. Processing row-by-row and checking predictions vs HC1/HC2…"
        )

        try:
            from cloudbot.data.training.load_training_csv import load_training_csv
            from cloudbot.eval.driver import extract_predicted_label
            from cloudbot.eval.compare import compare_one
            from cloudbot.eval.normalize import normalize_human_labels
            from cloudbot.eval.taxonomy import build_tier2_to_tier1, load_taxonomy_rows
            from cloudbot.pipeline.run_pipeline import run_autocoding_pipeline

            taxonomy_path = repo_root / "cloudbot" / "data" / "label-taxonomy.csv"
            taxonomy_rows = load_taxonomy_rows(taxonomy_path)
            tier2_to_tier1 = build_tier2_to_tier1(taxonomy_rows)
            examples = load_training_csv(testing_path)
        except Exception as e:
            await message.channel.send(f"Failed to load testing data or taxonomy: {e}")
            return

        total = 0
        correct = 0
        incorrect = 0

        for ex in examples:
            prompt = (ex.get("prompt") or "").strip()
            if not prompt:
                continue
            total += 1

            hc1 = ex.get("hc1") or []
            hc2 = ex.get("hc2") or []
            p1, _ = normalize_human_labels(list(hc1), tier2_to_tier1=tier2_to_tier1)
            p2, _ = normalize_human_labels(list(hc2), tier2_to_tier1=tier2_to_tier1)

            # golden = union of hc1+hc2 patterns
            seen = set()
            golden_patterns = []
            for p in (p1 + p2):
                k = (p.tier1, p.tier2, p.tier3)
                if k in seen:
                    continue
                seen.add(k)
                golden_patterns.append(p)
            if not golden_patterns:
                await message.channel.send(
                    f"**Row {total}** ⚪ no HC1/HC2 labels; skipping.\n"
                    f"- Prompt: `{prompt[:180] + ('…' if len(prompt) > 180 else '')}`"
                )
                continue

            pipeline_out = run_autocoding_pipeline(
                prompt,
                context={k: ex.get(k) for k in ("group", "timestamp-mm", "people", "context") if ex.get(k)},
            )
            predicted = extract_predicted_label(pipeline_out)
            comp = compare_one(predicted, golden_patterns)

            short_prompt = prompt[:180] + ("…" if len(prompt) > 180 else "")
            if comp.is_match:
                correct += 1
                await message.channel.send(
                    f"**Row {total}** ✅ correct\n"
                    f"- Prompt: `{short_prompt}`\n"
                    f"- Predicted: `{predicted}` (matches HC1/HC2)"
                )
            else:
                incorrect += 1
                await message.channel.send(
                    f"**Row {total}** ❌ incorrect\n"
                    f"- Prompt: `{short_prompt}`\n"
                    f"- Predicted: `{predicted}`\n"
                    f"- Note: does not match HC1 or HC2 (after normalization)."
                )

        if total == 0:
            await message.channel.send("Testing CSV has no usable rows (no prompts).")
            return

        accuracy = (correct / total) * 100.0
        await message.channel.send(
            f"Testing complete: **{correct}/{total}** correct, **{incorrect}** incorrect "
            f"(accuracy ≈ **{accuracy:.1f}%**; HC1/HC2 union as gold)."
        )


async def run_all_bots() -> None:
    """Start Controller + 4 display bots on one event loop. Requires 5 tokens in env."""
    try:
        from cloudbot.pipeline import run_autocoding_pipeline
    except ImportError:
        from cloudbot.discord.controller_example import run_autocoding_pipeline_placeholder as run_autocoding_pipeline

    tokens = _get_tokens()
    controller_token = tokens.pop("controller")
    if not controller_token:
        raise SystemExit(
            "Set DISCORD_CONTROLLER_TOKEN (or CONTROLLER_TOKEN / DISCORD_CONTROLLER_BOT_TOKEN) in the environment."
        )

    role_ids = [SIGNAL_EXTRACTOR, LABEL_CODER, BOUNDARY_CRITIC, ADJUDICATOR]
    display_bots: list[DisplayBot] = []
    for role_id in role_ids:
        token = tokens.get(role_id, "").strip()
        if not token:
            print(f"Warning: no token for {role_id}; that bot will not send.")
        display_bots.append(DisplayBot(role_id=role_id, intents=discord.Intents.default()))

    # Only register bots that have tokens — each role sends with its own client
    role_to_bot = {b.role_id: b for b in display_bots if tokens.get(b.role_id, "").strip()}
    if len(role_to_bot) < 4:
        print("  Using webhook fallback: the 4 roles will appear as Signal Extractor, Label Coder, Boundary Critic, Adjudicator (bot needs Manage Webhooks).")
    for rid in role_ids:
        if rid not in role_to_bot:
            print(f"  Missing token for {rid}; will send via webhook as '{ROLE_DISPLAY_NAMES.get(rid, rid)}'.")
    intents = discord.Intents.default()
    intents.message_content = True  # required to read message content
    controller = ControllerBot(
        role_to_bot=role_to_bot,
        run_pipeline=run_autocoding_pipeline,
        intents=intents,
    )

    # Run all 5 bots on the same event loop (bot.start() is a coroutine)
    loop = asyncio.get_event_loop()
    for i, role_id in enumerate(role_ids):
        token = tokens.get(role_id, "").strip()
        if token:
            loop.create_task(display_bots[i].start(token))
    loop.create_task(controller.start(controller_token))
    await asyncio.Future()  # run forever


def main() -> None:
    """Entry point: run 5 Discord bots (Controller + Signal, Label, Critic, Judge)."""
    try:
        asyncio.run(run_all_bots())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
