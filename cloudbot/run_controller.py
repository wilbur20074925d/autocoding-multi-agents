"""
Controller entry point for the autocoding Discord bot.

Use this module so the pipeline returns real output (evidence spans, labels,
critic, adjudicator) instead of placeholders.

  - run_autocoding_pipeline(prompt, context?) → pipeline_output
  - handle_discord_message(prompt, channel_id, ..., run_pipeline=..., send_as_bot=...)

Your Discord client should:
  1. On "Label this prompt:" (or your trigger), read the prompt and optionally strip
     the bot mention and prefix (the pipeline also strips <@id> and "Label this prompt:").
  2. Call handle_discord_message(prompt, channel_id, send_as_bot=your_send_fn).
     By default this uses the real pipeline from cloudbot.pipeline.
  3. Post the four bot messages in order.

Example (from your Discord runner):

  from cloudbot.run_controller import run_autocoding_pipeline, handle_discord_message

  async def on_message(message):
      if message.author.bot:
          return
      prompt = message.content
      await handle_discord_message(prompt, message.channel.id, send_as_bot=send_as_bot)
"""

from __future__ import annotations

from cloudbot.discord.controller_example import handle_discord_message
from cloudbot.pipeline import run_autocoding_pipeline

__all__ = ["run_autocoding_pipeline", "handle_discord_message"]
