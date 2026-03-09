"""
Discord runner: 1 Controller bot + 4 display bots (Signal, Label, Critic, Judge).

Flow:
  Controller receives "Label this prompt..." → run pipeline → send_as_bot(role)
    → SignalBot.send() | LabelBot.send() | CriticBot.send() | JudgeBot.send()

Requires discord.py and 5 bot tokens in env: CONTROLLER_TOKEN, SIGNAL_BOT_TOKEN,
LABEL_BOT_TOKEN, CRITIC_BOT_TOKEN, JUDGE_BOT_TOKEN.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import discord
from discord import Message

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


def _get_tokens() -> dict[str, str]:
    return {
        "controller": os.environ.get("CONTROLLER_TOKEN", "").strip(),
        SIGNAL_EXTRACTOR: os.environ.get("SIGNAL_BOT_TOKEN", "").strip(),
        LABEL_CODER: os.environ.get("LABEL_BOT_TOKEN", "").strip(),
        BOUNDARY_CRITIC: os.environ.get("CRITIC_BOT_TOKEN", "").strip(),
        ADJUDICATOR: os.environ.get("JUDGE_BOT_TOKEN", "").strip(),
    }


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

        prompt = content
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

        for role_id, text in messages:
            bot = self.role_to_bot.get(role_id)
            if bot is not None:
                await bot.send_to_channel(channel_id, text)
            else:
                # Fallback: send from controller (single-bot mode)
                await message.channel.send(f"**[{role_id}]**\n{text}")


async def run_all_bots() -> None:
    """Start Controller + 4 display bots on one event loop. Requires 5 tokens in env."""
    try:
        from cloudbot.pipeline import run_autocoding_pipeline
    except ImportError:
        from cloudbot.discord.controller_example import run_autocoding_pipeline_placeholder as run_autocoding_pipeline

    tokens = _get_tokens()
    controller_token = tokens.pop("controller")
    if not controller_token:
        raise SystemExit("Set CONTROLLER_TOKEN in the environment.")

    role_ids = [SIGNAL_EXTRACTOR, LABEL_CODER, BOUNDARY_CRITIC, ADJUDICATOR]
    display_bots: list[DisplayBot] = []
    for role_id in role_ids:
        token = tokens.get(role_id, "").strip()
        if not token:
            print(f"Warning: no token for {role_id}; that bot will not send.")
        display_bots.append(DisplayBot(role_id=role_id, intents=discord.Intents.default()))

    # Only register bots that have tokens (so we don't send from unconnected clients)
    role_to_bot = {b.role_id: b for b in display_bots if tokens.get(b.role_id, "").strip()}
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
