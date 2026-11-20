"""Choosing the AI engine: OpenAI in the cloud, or Moo Local on a computer.

GET  /ai           — the current choice
POST /ai           — {"mode": "cloud" | "local", "local_url"?, "local_name"?}
GET  /ai/discover  — Moo Local computers announced on the home network
POST /ai/test      — a quick story line and voice through the chosen engine
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
import urllib.request

from fastapi import APIRouter, HTTPException

import config as moo_config

from ..models import AiDiscoverResponse, AiEngine, AiSettings, AiTestResponse, AiUpdateRequest
from ..services import service_control

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ai")

SERVICE_TYPE = "_mooai._tcp"


def _settings() -> AiSettings:
    from assistant.ai_client import ai_mode, ai_ready

    cfg = moo_config.load_config()
    ai = cfg.get("ai") or {}
    return AiSettings(
        mode=ai_mode(cfg),
        local_url=ai.get("local_url"),
        local_name=ai.get("local_name"),
        has_openai_key=bool(cfg.get("openai_api_key")),
        ready=ai_ready(cfg),
    )


@router.get("", response_model=AiSettings)
def get_ai() -> AiSettings:
    return _settings()


@router.post("", response_model=AiSettings)
def post_ai(req: AiUpdateRequest) -> AiSettings:
    update: dict = {"mode": req.mode}
    if req.local_url is not None:
        update["local_url"] = req.local_url.rstrip("/")
    if req.local_name is not None:
        update["local_name"] = req.local_name
    moo_config.update_config({"ai": update})
    # The storyteller reads the engine when it starts.
    if service_control.is_assistant_running():
        service_control.restart_assistant()
    return _settings()


def _health(url: str) -> dict | None:
    base = url[: -len("/v1")] if url.endswith("/v1") else url
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=3) as r:
            return json.loads(r.read())
    except Exception:
        return None


@router.get("/discover", response_model=AiDiscoverResponse)
def discover() -> AiDiscoverResponse:
    """Find Moo Local on the network through mDNS (avahi-browse)."""
    try:
        out = subprocess.run(
            ["avahi-browse", "-rpt", SERVICE_TYPE],
            capture_output=True, text=True, timeout=8, check=False,
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        out = ""
    engines: dict[str, AiEngine] = {}
    for line in out.splitlines():
        # =;iface;proto;name;type;domain;host;address;port;txt
        f = line.split(";")
        if len(f) < 9 or f[0] != "=" or f[2] != "IPv4":
            continue
        name = f[3].replace("\\032", " ")
        host, address, port = f[6], f[7], f[8]
        url = f"http://{address}:{port}/v1"
        health = _health(url)
        engines[url] = AiEngine(
            name=name,
            host=host,
            url=url,
            reachable=health is not None,
            ready=bool(health and health.get("story_model_ready") and health.get("voice_ready")),
        )
    return AiDiscoverResponse(engines=list(engines.values()))


@router.post("/test", response_model=AiTestResponse)
def test() -> AiTestResponse:
    """One short line from the story model and the same line as Moo's voice."""
    from assistant.ai_client import ai_mode, make_client

    cfg = moo_config.load_config()
    try:
        client = make_client(cfg, timeout=60)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    started = time.time()
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Say hello to a child in one short, happy sentence."}],
            max_tokens=40,
        )
        line = (resp.choices[0].message.content or "").strip()
        story_s = time.time() - started
        t = time.time()
        speech = client.audio.speech.create(model="tts-1", voice="sage", input=line, response_format="wav")
        wav = speech.read() if hasattr(speech, "read") else speech.content
        voice_s = time.time() - t
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"The {ai_mode(cfg)} AI did not answer: {exc}") from exc
    return AiTestResponse(
        mode=ai_mode(cfg),
        line=line,
        story_seconds=round(story_s, 1),
        voice_seconds=round(voice_s, 1),
        voice_bytes=len(wav),
    )
