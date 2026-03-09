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
    """Read tokens from env. Each role uses its own bot client."""
    return {
        "controller": os.environ.get("DISCORD_CONTROLLER_TOKEN", os.environ.get("CONTROLLER_TOKEN", "")).strip(),
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
        if LABEL_PROMPT_TRIGGER not in content.lower():
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


async def run_all_bots() -> None:
    """Start Controller + 4 display bots on one event loop. Requires 5 tokens in env."""
    try:
        from cloudbot.pipeline import run_autocoding_pipeline
    except ImportError:
        from cloudbot.discord.controller_example import run_autocoding_pipeline_placeholder as run_autocoding_pipeline

    tokens = _get_tokens()
    controller_token = tokens.pop("controller")
    if not controller_token:
        raise SystemExit("Set DISCORD_CONTROLLER_TOKEN (or CONTROLLER_TOKEN) in the environment.")

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
