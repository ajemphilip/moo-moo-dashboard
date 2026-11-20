"""Validate an OpenAI API key by hitting the free models.list endpoint."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

VALIDATION_TIMEOUT_SECONDS = 10.0


def validate_api_key(key: str) -> tuple[bool, str | None]:
    if not key or not key.startswith("sk-"):
        return False, "API key must start with 'sk-'"
    try:
        import openai
    except ImportError:
        return False, "openai package not installed"
    try:
        client = openai.OpenAI(api_key=key, timeout=VALIDATION_TIMEOUT_SECONDS)
        client.models.list()
    except openai.AuthenticationError:
        return False, "Invalid API key"
    except openai.APIConnectionError:
        return False, "Could not reach OpenAI — check internet connection"
    except openai.RateLimitError:
        return False, "Rate limited by OpenAI — try again shortly"
    except Exception as exc:
        logger.exception("Unexpected error validating key")
        return False, f"Unexpected error: {exc.__class__.__name__}"
    return True, None
