"""Which AI the Moo talks to: OpenAI in the cloud, or Moo Local on a computer
on the home network.

Moo Local (local_ai/moo_local.py) answers the same requests in OpenAI's format,
so both are reached with the same OpenAI client. Only the address changes:

  cloud  — api.openai.com, with the OpenAI key from the configuration
  local  — config["ai"]["local_url"], for example http://Mac.local:8765/v1,
           no key and no internet needed
  browser — a phone or laptop lent to Moo through the Lend page. The work is
           queued on the Moo (setup_service/routes/engine.py) and the device
           does it in its browser. BrowserClient below looks like the parts
           of the OpenAI client the Moo uses, so nothing else changes.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)

LOCAL_KEY = "moo-local"  # Moo Local needs no key; the client library wants one


def ai_mode(cfg: dict[str, Any]) -> str:
    mode = (cfg.get("ai") or {}).get("mode")
    return mode if mode in ("local", "browser") else "cloud"


def local_url(cfg: dict[str, Any]) -> str | None:
    return (cfg.get("ai") or {}).get("local_url") or None


def ai_ready(cfg: dict[str, Any]) -> bool:
    """True when the chosen AI has what it needs: a key, or a local address."""
    if ai_mode(cfg) == "local":
        return bool(local_url(cfg))
    if ai_mode(cfg) == "browser":
        return True  # whether a device is lent right now is checked per job
    return bool(cfg.get("openai_api_key"))


def make_client(cfg: dict[str, Any], timeout: float = 120.0):
    """Return a client for the chosen AI, with the models and voice chosen on
    the AI engine screen applied. Raises ValueError if not set up."""
    ai = cfg.get("ai") or {}
    mode = ai_mode(cfg)
    if mode == "browser":
        return Configured(BrowserClient(timeout=timeout), voice=ai.get("voice"))

    import openai

    if mode == "local":
        url = local_url(cfg)
        if not url:
            raise ValueError("Local AI is chosen, but no computer is selected")
        base = openai.OpenAI(base_url=url, api_key=LOCAL_KEY, timeout=timeout)
        return Configured(
            base,
            chat_model=ai.get("story_model"),
            listen_model=ai.get("listen_model"),
            speech_model=ai.get("voice_engine"),
            voice=ai.get("voice"),
            extra_speech={
                "expressiveness": ai.get("expressiveness", 0.6),
                "quality": (ai.get("voice_qualities") or {}).get(ai.get("voice_engine") or "", ""),
            },
        )
    key = cfg.get("openai_api_key")
    if not key:
        raise ValueError("No OpenAI API key configured")
    base = openai.OpenAI(api_key=key, timeout=timeout)
    speech_model = ai.get("cloud_voice_model")
    return Configured(
        base,
        chat_model=ai.get("cloud_story_model"),
        speech_model=speech_model,
        # gpt-4o-mini-tts can be told how to speak.
        extra_speech=(
            {"instructions": STORYTELLER_INSTRUCTIONS} if speech_model == "gpt-4o-mini-tts" else None
        ),
    )


STORYTELLER_INSTRUCTIONS = (
    "You are Moo, a warm and playful storyteller for young children. Speak with "
    "lively expression: excited at surprises, gentle and soft at cosy moments, "
    "a little hushed for suspense, with clear words and natural pauses."
)


class Configured:
    """Wraps a client so the chosen story model, voice model and voice are used,
    whatever names the story code passes. Everything else goes straight through."""

    def __init__(self, client, chat_model=None, speech_model=None, voice=None, extra_speech=None,
                 listen_model=None):
        self._c = client
        self.moderations = client.moderations
        self.models = getattr(client, "models", None)
        chat_create = client.chat.completions.create
        speech_create = client.audio.speech.create
        listen_create = client.audio.transcriptions.create

        def listen(**kw):
            if listen_model:
                kw["model"] = listen_model
            return listen_create(**kw)

        def chat(**kw):
            if chat_model:
                kw["model"] = chat_model
            return chat_create(**kw)

        def speech(**kw):
            if speech_model:
                kw["model"] = speech_model
            if voice:
                kw["voice"] = voice
            if extra_speech:
                if isinstance(self._c, BrowserClient):
                    kw.update(extra_speech)
                else:
                    kw["extra_body"] = {**kw.get("extra_body", {}), **extra_speech}
            return speech_create(**kw)

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=chat))
        self.audio = SimpleNamespace(
            transcriptions=SimpleNamespace(create=listen),
            speech=SimpleNamespace(create=speech),
        )


# --- a lent browser ---------------------------------------------------------------

import base64
import json
import urllib.error
import urllib.request

ENGINE_SUBMIT = "http://127.0.0.1:8000/engine/submit"


def _submit(kind: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        ENGINE_SUBMIT,
        data=json.dumps({"kind": kind, "payload": payload, "timeout": timeout}).encode(),
        headers={"Content-Type": "application/json", "X-Moo-Setup": "1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout + 10) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("detail")
        except Exception:
            detail = str(exc)
        raise RuntimeError(detail) from None


class _Speech:
    def __init__(self, wav: bytes):
        self.content = wav

    def read(self) -> bytes:
        return self.content


class BrowserClient:
    """The parts of the OpenAI client the Moo uses, done by a lent browser."""

    def __init__(self, timeout: float = 120.0):
        self.timeout = timeout
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))
        self.audio = SimpleNamespace(
            transcriptions=SimpleNamespace(create=self._transcribe),
            speech=SimpleNamespace(create=self._speak),
        )
        self.moderations = SimpleNamespace(create=self._moderate)

    def _chat(self, model=None, messages=None, max_tokens=None, response_format=None, **_kw):
        res = _submit(
            "chat",
            {"messages": messages or [], "max_tokens": max_tokens, "response_format": response_format},
            self.timeout,
        )
        content = res.get("content", "")
        # What the lent device wrote, so its answers can be compared with a
        # computer's when the story goes wrong on a phone.
        logger.info("lent chat reply: %r", content[:240])
        msg = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    def _transcribe(self, model=None, file=None, **_kw):
        data = file.read() if hasattr(file, "read") else file
        res = _submit("transcribe", {"wav_b64": base64.b64encode(data).decode()}, self.timeout)
        return SimpleNamespace(text=res.get("text", ""))

    def _speak(self, model=None, voice=None, input="", response_format=None, **_kw):
        res = _submit("speak", {"text": input, "voice": voice}, self.timeout)
        return _Speech(base64.b64decode(res.get("wav_b64", "")))

    def _moderate(self, model=None, input="", **_kw):
        res = _submit("moderate", {"text": input}, self.timeout)
        return SimpleNamespace(results=[SimpleNamespace(flagged=bool(res.get("flagged")))])
