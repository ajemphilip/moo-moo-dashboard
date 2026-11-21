"""Moo Local — speech, story and voice for the Moo, computed on this computer.

The Moo normally calls OpenAI. In local mode it calls this server instead, on
the home network, with exactly the same requests. This server answers the
four kinds the Moo uses, in OpenAI's format, so the Pi code only changes the
address it talks to:

  POST /v1/chat/completions      story, games, checks   -> Ollama (gemma3:12b)
  POST /v1/audio/transcriptions  the child's speech     -> Whisper large-v3-turbo (mlx-whisper)
  POST /v1/audio/speech          Moo's voice            -> Kokoro (kokoro-onnx), Piper as fallback
  POST /v1/moderations           safety check           -> Ollama, yes or no
  GET  /health                   what is running here

It announces itself on the network as `_mooai._tcp`, so the Moo's setup page
finds it without anyone typing an address.

Run:  ~/.moo-local-venv/bin/python moo_local.py
"""

from __future__ import annotations

import io
import json
import logging
import os
import socket
import threading
import time
import wave
from pathlib import Path

import httpx
import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("moo-local")

PORT = int(os.environ.get("MOO_LOCAL_PORT", "8765"))
OLLAMA = os.environ.get("MOO_OLLAMA_URL", "http://127.0.0.1:11434")
CHAT_MODEL = os.environ.get("MOO_LOCAL_CHAT_MODEL", "gemma3:12b")
FALLBACK_CHAT_MODEL = "llama3.2:3b"
WHISPER_MODEL = os.environ.get("MOO_LOCAL_WHISPER", "mlx-community/whisper-large-v3-turbo")
KOKORO_DIR = Path(os.environ.get("MOO_LOCAL_KOKORO", str(Path.home() / ".moo-local" / "kokoro")))
KOKORO_VOICE = os.environ.get("MOO_LOCAL_KOKORO_VOICE", "af_heart")
VOICE_PATH = Path(
    os.environ.get(
        "MOO_LOCAL_VOICE",
        str(Path.home() / ".moo-local" / "voices" / "en_US-amy-medium.onnx"),
    )
)
SERVICE_TYPE = "_mooai._tcp.local."

app = FastAPI(title="Moo Local", version="0.1.0")
_whisper_lock = threading.Lock()
_voice = None
_kokoro = None
_voice_lock = threading.Lock()
_chat_model: str | None = None


async def _installed() -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            tags = (await client.get(f"{OLLAMA}/api/tags")).json()
        return [m.get("name") for m in tags.get("models", []) if m.get("name")]
    except Exception:
        return []


async def _model(requested: str | None = None) -> str:
    """The story model to use: the one the Moo asked for if it is installed here,
    else Gemma 3 12B, else Llama 3.2 3B."""
    global _chat_model
    names = await _installed()
    if requested and requested in names:
        return requested
    if not _chat_model:
        _chat_model = CHAT_MODEL if CHAT_MODEL in names else FALLBACK_CHAT_MODEL
        log.info("default story model: %s", _chat_model)
    return _chat_model


# --- story, games and checks -------------------------------------------------

@app.post("/v1/chat/completions")
async def chat(request: Request) -> JSONResponse:
    body = await request.json()
    # The Moo names a model it chose on its AI engine screen, or an OpenAI name
    # (gpt-4o-mini) when nothing was chosen; then the default model answers.
    body["model"] = await _model(body.get("model"))
    body.pop("stream", None)
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{OLLAMA}/v1/chat/completions", json=body)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Ollama: {r.text[:200]}")
    return JSONResponse(r.json())


@app.post("/v1/moderations")
async def moderations(request: Request) -> JSONResponse:
    """A local stand-in for OpenAI's moderation: the story model judges the text."""
    body = await request.json()
    text = body.get("input", "")
    if isinstance(text, list):
        text = " ".join(str(t) for t in text)
    flagged = False
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{OLLAMA}/v1/chat/completions",
                json={
                    "model": await _model(),
                    "response_format": {"type": "json_object"},
                    "max_tokens": 20,
                    "messages": [
                        {
                            "role": "system",
                            "content": 'Answer as JSON with one key, "safe", true or false.',
                        },
                        {
                            "role": "user",
                            "content": "Is this text safe to read aloud to a young child? "
                            "It must not contain violence, fear, cruelty, adult themes "
                            f"or rude language.\n\n{text}",
                        },
                    ],
                },
            )
        data = json.loads(r.json()["choices"][0]["message"]["content"] or "{}")
        flagged = not bool(data.get("safe", True))
    except Exception:
        log.warning("local moderation failed; treating the text as safe", exc_info=True)
    return JSONResponse(
        {
            "id": "modr-local",
            "model": "moo-local",
            "results": [{"flagged": flagged, "categories": {}, "category_scores": {}}],
        }
    )


# --- the child's speech ------------------------------------------------------

def _wav_to_float16k(data: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(data), "rb") as wf:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())
    if width != 2:
        raise HTTPException(status_code=400, detail="Only 16-bit WAV is supported")
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != 16000 and audio.size:
        n = int(round(audio.size * 16000 / rate))
        audio = np.interp(
            np.linspace(0, audio.size - 1, n), np.arange(audio.size), audio
        ).astype(np.float32)
    return audio


@app.post("/v1/audio/transcriptions")
def transcriptions(
    file: UploadFile = File(...),
    model: str = Form("whisper-1"),
    language: str | None = Form(None),
) -> JSONResponse:
    import mlx_whisper

    audio = _wav_to_float16k(file.file.read())
    if audio.size < 1600:  # under 0.1 s
        return JSONResponse({"text": ""})
    started = time.time()
    with _whisper_lock:
        result = mlx_whisper.transcribe(audio, path_or_hf_repo=WHISPER_MODEL)
    text = (result.get("text") or "").strip()
    log.info("heard in %.1fs: %r", time.time() - started, text)
    return JSONResponse({"text": text})


# --- Moo's voice -------------------------------------------------------------

def _get_voice():
    global _voice
    if _voice is None:
        from piper import PiperVoice

        _voice = PiperVoice.load(str(VOICE_PATH))
    return _voice


def _get_kokoro():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro

        _kokoro = Kokoro(str(KOKORO_DIR / "kokoro-v1.0.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))
    return _kokoro


_chatterbox = None


def _chatterbox_say(text: str, expressiveness: float) -> tuple[int, np.ndarray]:
    """Chatterbox, the expressive voice. `expressiveness` 0.25 calm to 1.0 dramatic."""
    global _chatterbox
    import torch
    from chatterbox.tts import ChatterboxTTS

    if _chatterbox is None:
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        _chatterbox = ChatterboxTTS.from_pretrained(device=device)
    x = max(0.25, min(1.0, expressiveness))
    wav = _chatterbox.generate(text, exaggeration=x, cfg_weight=0.5 if x < 0.7 else 0.3)
    audio = wav.squeeze().detach().cpu().numpy()
    return _chatterbox.sr, (np.clip(audio, -1, 1) * 32767).astype(np.int16)


def _free_chatterbox() -> None:
    """Chatterbox holds several GB. Let it go as soon as another voice is used,
    so the story model has the memory back."""
    global _chatterbox
    if _chatterbox is None:
        return
    _chatterbox = None
    try:
        import gc

        import torch

        gc.collect()
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except Exception:
        pass
    log.info("chatterbox unloaded")


def _kokoro_ready() -> bool:
    return (KOKORO_DIR / "kokoro-v1.0.onnx").exists() and (KOKORO_DIR / "voices-v1.0.bin").exists()


@app.post("/v1/audio/speech")
async def speech(request: Request) -> Response:
    body = await request.json()
    text = (body.get("input") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="No text")
    started = time.time()
    engine = str(body.get("model") or "")
    voice_id = str(body.get("voice") or "")
    if voice_id not in {v for v, _ in KOKORO_VOICES}:
        voice_id = KOKORO_VOICE
    buf = io.BytesIO()
    with _voice_lock:
        if engine != "chatterbox":
            _free_chatterbox()
        if engine == "chatterbox" and _chatterbox_ready():
            rate, pcm = _chatterbox_say(text, float(body.get("expressiveness") or 0.6))
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(rate)
                wf.writeframes(pcm.tobytes())
        elif _kokoro_ready():
            lang = "en-gb" if voice_id.startswith("b") else "en-us"
            samples, rate = _get_kokoro().create(text, voice=voice_id, speed=1.0, lang=lang)
            pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(rate)
                wf.writeframes(pcm.tobytes())
        else:
            voice = _get_voice()
            with wave.open(buf, "wb") as wf:
                voice.synthesize_wav(text, wf)
    log.info("spoke %d characters in %.1fs", len(text), time.time() - started)
    return Response(content=buf.getvalue(), media_type="audio/wav")


# --- what can be chosen here ---------------------------------------------------

# Kokoro voices offered on the Moo's AI engine screen (id, label).
KOKORO_VOICES = [
    ("af_heart", "Heart — warm"),
    ("af_bella", "Bella — bright"),
    ("af_nicole", "Nicole — soft"),
    ("af_sarah", "Sarah — calm"),
    ("af_sky", "Sky — light"),
    ("bf_emma", "Emma — British"),
    ("bf_isabella", "Isabella — British"),
    ("am_puck", "Puck — playful"),
    ("am_michael", "Michael — gentle"),
    ("bm_george", "George — British storyteller"),
]

_STORY_LABELS = {
    "gemma3:12b": "Gemma 3 12B — best stories, about 6 s",
    "llama3.2:3b": "Llama 3.2 3B — fastest, simpler stories",
    "llama3.1:8b": "Llama 3.1 8B — good and quick",
    "qwen2.5:7b": "Qwen 2.5 7B — good and quick",
}


def _chatterbox_ready() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("chatterbox") is not None
    except Exception:
        return False


@app.get("/moo/options")
async def options() -> dict:
    """Story models installed here, voice engines and voices, for the Moo's screen."""
    names = [n for n in await _installed() if "coder" not in n and "embed" not in n]
    engines = [{"id": "kokoro", "label": "Kokoro — clear and quick"}] if _kokoro_ready() else []
    if _chatterbox_ready():
        engines.append({
            "id": "chatterbox",
            "label": "Chatterbox — most expressive, very slow here (20–60 s a sentence)",
        })
    if not engines:
        engines.append({"id": "piper", "label": "Piper — basic"})
    return {
        "story_models": [{"id": n, "label": _STORY_LABELS.get(n, n)} for n in names],
        "default_story_model": await _model(),
        "voice_engines": engines,
        "voices": [{"id": v, "label": l} for v, l in KOKORO_VOICES] if _kokoro_ready() else [],
    }


# --- status ------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    ollama_ok = False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            tags = (await client.get(f"{OLLAMA}/api/tags")).json()
        names = {m.get("name") for m in tags.get("models", [])}
        ollama_ok = CHAT_MODEL in names or FALLBACK_CHAT_MODEL in names
    except Exception:
        pass
    return {
        "service": "moo-local",
        "version": "0.1.0",
        "computer": socket.gethostname(),
        "story_model": _chat_model or CHAT_MODEL,
        "story_model_ready": ollama_ok,
        "speech_model": WHISPER_MODEL,
        "voice": f"kokoro {KOKORO_VOICE}" if _kokoro_ready() else VOICE_PATH.stem,
        "voice_ready": _kokoro_ready() or VOICE_PATH.exists(),
    }


# --- announce on the network -------------------------------------------------

def _lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # no packet is sent; picks the LAN interface
        return s.getsockname()[0]
    finally:
        s.close()


def _announce():
    from zeroconf import ServiceInfo, Zeroconf

    host = socket.gethostname().split(".")[0]
    info = ServiceInfo(
        SERVICE_TYPE,
        f"Moo Local on {host}.{SERVICE_TYPE}",
        addresses=[socket.inet_aton(_lan_ip())],
        port=PORT,
        properties={"path": "/v1", "version": "0.1.0"},
        server=f"{host}.local.",
    )
    zc = Zeroconf()
    zc.register_service(info)
    log.info("announced as %r on %s:%d", info.name, _lan_ip(), PORT)
    return zc, info


if __name__ == "__main__":
    zc, info = _announce()
    try:
        uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
    finally:
        zc.unregister_service(info)
        zc.close()
