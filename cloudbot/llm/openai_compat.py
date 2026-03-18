from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OpenAICompatConfig:
    api_key: str
    base_url: str
    model: str
    timeout_s: float = 60.0


def load_config_from_env() -> OpenAICompatConfig | None:
    api_key = (os.environ.get("OPENAI_API_KEY") or os.environ.get("CLOUDBOT_OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None
    base_url = (os.environ.get("OPENAI_BASE_URL") or os.environ.get("CLOUDBOT_OPENAI_BASE_URL") or "https://api.openai.com/v1").strip()
    model = (os.environ.get("OPENAI_MODEL") or os.environ.get("CLOUDBOT_OPENAI_MODEL") or "gpt-4o-mini").strip()
    timeout_s = float(os.environ.get("CLOUDBOT_OPENAI_TIMEOUT_S", "60").strip() or "60")
    return OpenAICompatConfig(api_key=api_key, base_url=base_url.rstrip("/"), model=model, timeout_s=timeout_s)


def chat_completions_json(
    *,
    cfg: OpenAICompatConfig,
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    max_tokens: int = 1200,
) -> dict[str, Any]:
    """
    OpenAI-compatible Chat Completions call.
    Returns parsed JSON response from the assistant content.
    """
    url = f"{cfg.base_url}/chat/completions"
    payload: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }

    req = urllib.request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout_s) as resp:
            raw = resp.read().decode("utf-8")
    except (urllib.error.HTTPError, urllib.error.URLError, socket.timeout) as e:
        raise RuntimeError(f"OpenAI-compatible request failed: {e}") from e

    data = json.loads(raw)
    content = (
        (((data.get("choices") or [{}])[0] or {}).get("message") or {}).get("content")
        or ""
    )
    if not content.strip():
        raise RuntimeError("Empty assistant content from LLM")
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Assistant did not return valid JSON: {e}") from e

