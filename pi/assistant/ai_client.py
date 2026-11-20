"""Which AI the Moo talks to: OpenAI in the cloud, or Moo Local on a computer
on the home network.

Moo Local (local_ai/moo_local.py) answers the same requests in OpenAI's format,
so both are reached with the same OpenAI client. Only the address changes:

  cloud  — api.openai.com, with the OpenAI key from the configuration
  local  — config["ai"]["local_url"], for example http://Mac.local:8765/v1,
           no key and no internet needed
"""

from __future__ import annotations

from typing import Any

LOCAL_KEY = "moo-local"  # Moo Local needs no key; the client library wants one


def ai_mode(cfg: dict[str, Any]) -> str:
    return "local" if (cfg.get("ai") or {}).get("mode") == "local" else "cloud"


def local_url(cfg: dict[str, Any]) -> str | None:
    return (cfg.get("ai") or {}).get("local_url") or None


def ai_ready(cfg: dict[str, Any]) -> bool:
    """True when the chosen AI has what it needs: a key, or a local address."""
    if ai_mode(cfg) == "local":
        return bool(local_url(cfg))
    return bool(cfg.get("openai_api_key"))


def make_client(cfg: dict[str, Any], timeout: float = 120.0):
    """Return an OpenAI client for the chosen AI. Raises ValueError if not set up."""
    import openai

    if ai_mode(cfg) == "local":
        url = local_url(cfg)
        if not url:
            raise ValueError("Local AI is chosen, but no computer is selected")
        return openai.OpenAI(base_url=url, api_key=LOCAL_KEY, timeout=timeout)
    key = cfg.get("openai_api_key")
    if not key:
        raise ValueError("No OpenAI API key configured")
    return openai.OpenAI(api_key=key, timeout=timeout)
