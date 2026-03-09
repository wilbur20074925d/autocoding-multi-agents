"""
Controller entry point: run pipeline and 5 Discord bots.

Flow:
  Controller
    │
    ▼
  run pipeline  (signal_extractor → label_coder → boundary_critic → adjudicator)
    │
    ▼
  send_as_bot(role)
    │
    ├ SignalBot.send()   → "Evidence spans..."
    ├ LabelBot.send()    → "Candidate labels..."
    ├ CriticBot.send()   → "Boundary conflict..."
    └ JudgeBot.send()   → "Final label..."

Start with: python -m cloudbot.run_controller
  (or from cloudbot/: python run_controller.py)

Requires 5 bot tokens in env:
  CONTROLLER_TOKEN, SIGNAL_BOT_TOKEN, LABEL_BOT_TOKEN, CRITIC_BOT_TOKEN, JUDGE_BOT_TOKEN

After startup, Discord shows 5 bots. When a user sends:
  "Label this prompt: <text>"
the Controller runs the pipeline and the 4 display bots post in order:
  Signal Extractor → Label Coder → Boundary Critic → Adjudicator.
"""

from __future__ import annotations

from cloudbot.discord.controller_example import handle_discord_message
from cloudbot.pipeline import run_autocoding_pipeline

def run_discord_bots() -> None:
    """Start Controller + 4 display bots. Requires discord.py and 5 tokens in env."""
    try:
        from cloudbot.discord.runner import main
    except ImportError as e:
        if "discord" in str(e).lower():
            raise SystemExit("Install discord.py: pip install discord.py") from e
        raise
    main()


__all__ = [
    "run_autocoding_pipeline",
    "handle_discord_message",
    "run_discord_bots",
]


if __name__ == "__main__":
    run_discord_bots()
