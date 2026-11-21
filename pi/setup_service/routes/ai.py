"""Choosing the AI engine: OpenAI in the cloud, or Moo Local on a computer.

GET  /ai           — the current choice
POST /ai           — {"mode"?, "local_url"?, "local_name"?, and the choices below}
GET  /ai/options   — what can be chosen for the current engine: story models,
                     voice engines, voices and quality
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
        tier=ai.get("tier"),
        story_model=ai.get("story_model"),
        listen_model=ai.get("listen_model"),
        voice_engine=ai.get("voice_engine"),
        voice=ai.get("voice"),
        expressiveness=float(ai.get("expressiveness", 0.6)),
        voice_qualities=dict(ai.get("voice_qualities") or {}),
        browser_quality=ai.get("browser_quality", "auto"),
        clarity_db=float((cfg.get("audio") or {}).get("clarity_db", 12.0)),
        cloud_story_model=ai.get("cloud_story_model"),
        cloud_voice_model=ai.get("cloud_voice_model"),
    )


@router.get("", response_model=AiSettings)
def get_ai() -> AiSettings:
    return _settings()


@router.post("", response_model=AiSettings)
def post_ai(req: AiUpdateRequest) -> AiSettings:
    update = req.model_dump(exclude_none=True)
    if "local_url" in update:
        update["local_url"] = update["local_url"].rstrip("/")
    if "expressiveness" in update:
        update["expressiveness"] = max(0.25, min(1.0, float(update["expressiveness"])))
    if "clarity_db" in update:  # belongs to the sound settings, not the engine
        clarity = max(0.0, min(18.0, float(update.pop("clarity_db"))))
        moo_config.update_config({"audio": {"clarity_db": clarity}})
    if update:
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
    # A computer added by its address stays in the list even when it does
    # not announce itself on the network.
    ai = moo_config.load_config().get("ai") or {}
    saved = (ai.get("local_url") or "").rstrip("/")
    if saved and saved not in engines:
        health = _health(saved)
        engines[saved] = AiEngine(
            name=ai.get("local_name") or saved,
            host=saved.split("//")[-1].split(":")[0],
            url=saved,
            reachable=health is not None,
            ready=bool(health and health.get("story_model_ready") and health.get("voice_ready")),
        )
    return AiDiscoverResponse(engines=list(engines.values()))


def _play_on_moo(cfg: dict, client, wav: bytes) -> None:
    """Play the test line through the Moo's own speaker, so the voice can be heard."""
    try:
        from assistant.audio import AudioIO

        audio_cfg = cfg.get("audio", {}) or {}
        AudioIO(client, output_device=audio_cfg.get("output_index"))._play_wav(wav)
    except Exception:
        logger.warning("could not play the test line on the Moo", exc_info=True)


@router.post("/test", response_model=AiTestResponse)
def test() -> AiTestResponse:
    """One short line from the story model and the same line as Moo's voice."""
    from assistant.ai_client import ai_mode, make_client

    cfg = moo_config.load_config()
    try:
        client = make_client(cfg, timeout=200)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    started = time.time()
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": "You are Moo, a storyteller cow. Say one short, happy, excited "
                "sentence to a child about the story you are going to tell. Reply with only that sentence.",
            }],
            max_tokens=40,
        )
        line = (resp.choices[0].message.content or "").strip()
        story_s = time.time() - started
        t = time.time()
        prefs = cfg.get("preferences", {}) or {}
        speech = client.audio.speech.create(
            model="tts-1", voice=prefs.get("tts_voice", "sage"), input=line, response_format="wav"
        )
        wav = speech.read() if hasattr(speech, "read") else speech.content
        voice_s = time.time() - t
        _play_on_moo(cfg, client, wav)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"The {ai_mode(cfg)} AI did not answer: {exc}") from exc
    return AiTestResponse(
        mode=ai_mode(cfg),
        line=line,
        story_seconds=round(story_s, 1),
        voice_seconds=round(voice_s, 1),
        voice_bytes=len(wav),
    )


# Voices that both Moo Local (kokoro-onnx) and the browser (kokoro-js) have.
KOKORO_VOICES = [
    {"id": "af_heart", "label": "Heart — warm"},
    {"id": "af_bella", "label": "Bella — bright"},
    {"id": "af_nicole", "label": "Nicole — soft"},
    {"id": "af_sarah", "label": "Sarah — calm"},
    {"id": "af_sky", "label": "Sky — light"},
    {"id": "bf_emma", "label": "Emma — British"},
    {"id": "bf_isabella", "label": "Isabella — British"},
    {"id": "am_puck", "label": "Puck — playful"},
    {"id": "am_michael", "label": "Michael — gentle"},
    {"id": "bm_george", "label": "George — British storyteller"},
]

OPENAI_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer"]


@router.get("/options")
def options() -> dict:
    """What can be chosen for the engine in use, for the AI engine screen."""
    from assistant.ai_client import ai_mode, local_url

    cfg = moo_config.load_config()
    mode = ai_mode(cfg)
    if mode == "cloud":
        return {
            "mode": mode,
            "story_models": [
                {"id": "gpt-4o-mini", "label": "GPT-4o mini — quick and cheap"},
                {"id": "gpt-4o", "label": "GPT-4o — richer stories, costs more"},
            ],
            "voice_engines": [
                {"id": "tts-1", "label": "Standard voice"},
                {"id": "gpt-4o-mini-tts", "label": "Expressive storyteller voice"},
            ],
            "voices": [{"id": v, "label": v.capitalize()} for v in OPENAI_VOICES],
            "voice_is_preference": True,
        }
    if mode == "browser":
        return {
            "mode": mode,
            "qualities": [
                {"id": "instant", "label": "Instant — Qwen 2.5 0.5B and the smallest Whisper, plain stories"},
                {"id": "auto", "label": "Automatic — small on phones, larger on computers"},
                {"id": "fast", "label": "Fast — Llama 3.2 1B, about 1 GB"},
                {"id": "better", "label": "Better — Llama 3.2 3B, about 2.5 GB, computers"},
            ],
            "voice_engines": [{"id": "kokoro", "label": "Kokoro"}],
            "voices": KOKORO_VOICES,
        }
    url = local_url(cfg) or ""
    base = url[: -len("/v1")] if url.endswith("/v1") else url
    try:
        with urllib.request.urlopen(f"{base}/moo/options", timeout=5) as r:
            data = json.loads(r.read())
    except Exception:
        data = {"story_models": [], "voice_engines": [], "voices": [], "unreachable": True}
    data["mode"] = mode
    return data
