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
import re
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
    repo = dict((k, r) for k, r, _ in LISTEN_MODELS).get(model, WHISPER_MODEL)
    with _whisper_lock:
        result = mlx_whisper.transcribe(audio, path_or_hf_repo=repo)
    text = (result.get("text") or "").strip()
    log.info("heard in %.1fs with %s: %r", time.time() - started, repo.split("/")[-1], text)
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
    quality = str(body.get("quality") or "") or None
    if engine == "pocket" and not _pocket_ready():
        engine = "kokoro"
    if engine == "supertonic" and not _supertonic_ready():
        engine = "kokoro"
    if engine == "mac" and voice_id in {v for v, _ in MAC_VOICES}:
        pass
    elif engine == "piper" and voice_id in _piper_voices():
        pass
    elif engine in ("mac", "piper"):
        voice_id = (MAC_VOICES[0][0] if engine == "mac" else next(iter(_piper_voices()), ""))
    elif engine == "pocket":
        voice_id = voice_id if voice_id in {v for v, _ in POCKET_VOICES} else POCKET_VOICES[0][0]
    elif engine == "supertonic":
        voice_id = voice_id if voice_id in {v for v, _ in SUPERTONIC_VOICES} else SUPERTONIC_VOICES[0][0]
    elif engine not in ("orpheus", "qwen3", "kitten2", "kitten2s") and voice_id not in {v for v, _ in KOKORO_VOICES}:
        voice_id = KOKORO_VOICE
    if engine != "orpheus":
        text = re.sub(r"<(giggle|laugh|chuckle|sigh|gasp|groan|yawn|cough|sniffle)>", "", text).strip()
    buf = io.BytesIO()
    with _voice_lock:
        if engine != "chatterbox":
            _free_chatterbox()
        orpheus_audio = None
        mlx_ok = {"orpheus": _orpheus_ready, "qwen3": _qwen3_ready}
        if (engine == "kitten2" and _kitten2_ready()) or (engine == "kitten2s" and _kitten2s_ready()):
            try:
                repo = "KittenML/kitten-tts-2" if engine == "kitten2" else _quality("kitten2s", quality)
                orpheus_audio = _kitten2_say(text, voice_id, repo)
            except Exception:
                log.warning("%s failed, this line in Kokoro", engine, exc_info=True)
                voice_id = KOKORO_VOICE
        if engine in mlx_ok and mlx_ok[engine]():
            try:
                orpheus_audio = _orpheus_say(text, voice_id, engine, _quality(engine, quality))
            except Exception:
                log.warning("%s failed twice, this line in Kokoro", engine, exc_info=True)
                text = re.sub(r"<[a-z]+>", "", text).strip()
                voice_id = KOKORO_VOICE
        if engine == "mac" and voice_id:
            rate, pcm = _mac_say(text, voice_id)
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(rate)
                wf.writeframes(pcm.tobytes())
        elif engine == "piper" and voice_id:
            with wave.open(buf, "wb") as wf:
                _get_piper(voice_id).synthesize_wav(text, wf)
        elif engine in ("pocket", "supertonic"):
            rate, pcm = _pocket_say(text, voice_id) if engine == "pocket" else _supertonic_say(
                text, voice_id, _quality("supertonic", quality) or 8)
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(rate)
                wf.writeframes(pcm.tobytes())
        elif orpheus_audio is not None:
            rate, pcm = orpheus_audio
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(rate)
                wf.writeframes(pcm.tobytes())
        elif engine == "chatterbox" and _chatterbox_ready():
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
    log.info("spoke %d characters with %s%s in %.1fs", len(text), engine or "kokoro",
             f" ({quality})" if quality else "", time.time() - started)
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



_ready_cache: dict[str, bool] = {}


def _importable(module: str) -> bool:
    if module not in _ready_cache:
        try:
            __import__(module)
            _ready_cache[module] = True
        except Exception:
            log.warning("%s is not usable here", module, exc_info=True)
            _ready_cache[module] = False
    return _ready_cache[module]


def _chatterbox_ready() -> bool:
    return _importable("chatterbox.tts")


def _orpheus_ready() -> bool:
    return _importable("mlx_audio.tts.utils")


ORPHEUS_MODEL = os.environ.get("MOO_LOCAL_ORPHEUS", "mlx-community/orpheus-3b-0.1-ft-4bit")
ORPHEUS_VOICES = [
    ("tara", "Tara — warm"), ("leah", "Leah — bright"), ("jess", "Jess — lively"),
    ("mia", "Mia — gentle"), ("zoe", "Zoe — young"), ("leo", "Leo — friendly"),
    ("dan", "Dan — easygoing"), ("zac", "Zac — cheerful"),
]
_orpheus = None  # the worker process


def _orpheus_worker():
    """The MLX voices run in their own process (mlx_voice_worker.py). Inside this
    process it shared the GPU with Whisper and the GPU timed out."""
    global _orpheus
    import subprocess
    import sys

    if _orpheus is None or _orpheus.poll() is not None:
        _orpheus = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name("mlx_voice_worker.py"))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
        )
        ready = _orpheus.stdout.readline()
        if not ready:
            raise RuntimeError("Orpheus worker did not start")
        log.info("orpheus worker started")
    return _orpheus


QWEN3_VOICES = [
    ("serena", "Serena — gentle"), ("vivian", "Vivian — bright"),
    ("ryan", "Ryan — lively"), ("aiden", "Aiden — clear"),
]
# Kitten TTS 2 (KittenML, September 2026). The full model has 1.7 billion
# parameters and voices made for stories; the small models are tiny and quick.
KITTEN2_VOICES = [
    ("FairytaleNarratorWhimsical", "Fairytale narrator"), ("GrandmaBedtimeStory", "Grandma, bedtime story"),
    ("AudiobookNarratorFemale", "Audiobook narrator"), ("WeatherPresenterCheerful", "Cheerful presenter"),
    ("Bruno", "Bruno"), ("Kiki", "Kiki"), ("Luna", "Luna"), ("Rosie", "Rosie"), ("Bella", "Bella"),
    ("Hugo", "Hugo"), ("Jasper", "Jasper"), ("Leo", "Leo"),
    ("Eleanor", "Eleanor"), ("Grace", "Grace"), ("Willow", "Willow"), ("Otis", "Otis"),
]
# English voices that all three small models share, then those only some have.
KITTEN2S_VOICES = [
    ("Eleanor", "Eleanor"), ("Edith", "Edith"), ("Otis", "Otis"),
    ("Grace", "Grace — not in Pico"), ("Serena", "Serena — not in Pico"), ("Iris", "Iris — not in Pico"),
    ("Martha", "Martha — not in Pico"), ("Vincent", "Vincent — not in Pico"),
    ("Max", "Max — not in Pico"), ("Danny", "Danny — not in Pico"),
]
_KITTEN2S_PICO = {"Eleanor", "Edith", "Otis", "Frank"}
KITTEN2_PYTHON = Path(os.environ.get("MOO_LOCAL_KITTEN2_PYTHON", str(Path.home() / ".moo-kitten2-venv" / "bin" / "python")))
_kitten2 = None  # the worker process


def _kitten2_ready() -> bool:
    return KITTEN2_PYTHON.exists() and _cached("KittenML/kitten-tts-2")


def _kitten2s_ready() -> bool:
    return KITTEN2_PYTHON.exists() and _cached("KittenML/kitten-tts-2-nano")


def _kitten2_say(text: str, voice: str, repo: str) -> tuple[int, np.ndarray]:
    """A voice from the Kitten TTS 2 worker (kitten2_worker.py), which runs
    in its own Python environment."""
    global _kitten2
    import subprocess
    import tempfile

    full = repo == "KittenML/kitten-tts-2"
    allowed = KITTEN2_VOICES if full else KITTEN2S_VOICES
    if voice not in {v for v, _ in allowed} or (repo.endswith("-pico") and voice not in _KITTEN2S_PICO):
        voice = allowed[0][0]
    if _kitten2 is None or _kitten2.poll() is not None:
        _kitten2 = subprocess.Popen(
            [str(KITTEN2_PYTHON), str(Path(__file__).with_name("kitten2_worker.py"))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
        )
        if not _kitten2.stdout.readline():
            raise RuntimeError("Kitten TTS 2 worker did not start")
        log.info("kitten 2 worker started")
    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "say.wav")
        _kitten2.stdin.write(json.dumps({"repo": repo, "text": text, "voice": voice, "out": out}) + "\n")
        _kitten2.stdin.flush()
        answer = json.loads(_kitten2.stdout.readline() or '{"ok": false, "error": "worker stopped"}')
        if not answer.get("ok"):
            raise RuntimeError(f"Kitten TTS 2: {answer.get('error')}")
        with wave.open(out, "rb") as wf:
            return wf.getframerate(), np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)


def _cached(repo: str) -> bool:
    return (Path.home() / ".cache" / "huggingface" / "hub" / ("models--" + repo.replace("/", "--"))).exists()


# Each voice model's own quality settings (id, label, what it means here).
# The last one that is installed is the default, as before.
VOICE_QUALITIES = {
    "kitten2s": [
        ("pico", "Pico — 7M, quickest", "KittenML/kitten-tts-2-pico"),
        ("nano", "Nano — 17M", "KittenML/kitten-tts-2-nano"),
        ("mini", "Mini — 82M, best", "KittenML/kitten-tts-2-mini"),
    ],
    "supertonic": [
        ("fast", "Fast — 3 steps", 3),
        ("normal", "Normal — 5 steps", 5),
        ("fine", "Fine — 8 steps, cleanest", 8),
    ],
    "qwen3": [
        ("small", "Small — 0.6B, quicker", "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"),
        ("large", "Large — 1.7B, richer, slower", "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"),
    ],
}
_DEFAULT_QUALITY = {"kitten2s": "nano", "supertonic": "fine", "qwen3": "small"}


def _qualities(engine: str) -> list[tuple]:
    """The quality settings of `engine` that can be used on this computer."""
    return [q for q in VOICE_QUALITIES.get(engine, []) if not isinstance(q[2], str) or _cached(q[2])]


def _quality(engine: str, wanted: str | None):
    """The setting behind a quality id: a model for Kitten and Qwen3, a
    number of steps for Supertonic. Falls back to the default."""
    have = {q[0]: q[2] for q in _qualities(engine)}
    return have.get(wanted or "") or have.get(_DEFAULT_QUALITY.get(engine, "")) or next(iter(have.values()), None)


def _qwen3_ready() -> bool:
    return _orpheus_ready() and _cached("mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit")


def _orpheus_say(text: str, voice: str, engine: str = "orpheus", repo: str | None = None) -> tuple[int, np.ndarray]:
    """A voice from the MLX worker. Orpheus is the expressive one that
    understands <giggle>, <laugh>, <chuckle>, <sigh>, <gasp>, <groan>, <yawn>
    written into the text; Qwen3 reads as a storyteller."""
    import tempfile

    allowed = {"orpheus": ORPHEUS_VOICES, "qwen3": QWEN3_VOICES}[engine]
    if voice not in {v for v, _ in allowed}:
        voice = allowed[0][0]
    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "say.wav")
        # With Gemma 12B also in memory on a 16 GB Mac the GPU sometimes
        # times out once; the second try nearly always works.
        for attempt in (1, 2):
            worker = _orpheus_worker()
            worker.stdin.write(json.dumps({"engine": engine, "repo": repo, "text": text, "voice": voice, "out": out}) + "\n")
            worker.stdin.flush()
            answer = json.loads(worker.stdout.readline() or '{"ok": false, "error": "worker stopped"}')
            if answer.get("ok"):
                break
            log.warning("%s try %d failed: %s", engine, attempt, answer.get("error"))
        else:
            raise RuntimeError(f"{engine}: {answer.get('error')}")
        with wave.open(out, "rb") as wf:
            rate = wf.getframerate()
            pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    return rate, pcm


# --- the faster voices and the tiers ------------------------------------------

# The Mac's own voices: instant, built into macOS (id, label).
MAC_VOICES = [
    ("Samantha", "Samantha — US, clear"),
    ("Flo (English (US))", "Flo — US, young"),
    ("Sandy (English (US))", "Sandy — US, friendly"),
    ("Shelley (English (UK))", "Shelley — British"),
    ("Daniel", "Daniel — British"),
    ("Karen", "Karen — Australian"),
    ("Moira", "Moira — Irish"),
    ("Grandma (English (US))", "Grandma — US"),
    ("Grandpa (English (UK))", "Grandpa — British"),
    ("Eddy (English (US))", "Eddy — US"),
]

_PIPER_LABELS = {
    "en_US-lessac-medium": "Lessac — US, clear",
    "en_US-ryan-high": "Ryan — US, warm",
    "en_US-kristin-medium": "Kristin — US, bright",
    "en_US-hfc_female-medium": "HFC — US, soft",
    "en_US-joe-medium": "Joe — US, calm",
    "en_US-kathleen-low": "Kathleen — US, quickest",
    "en_US-libritts_r-medium": "LibriTTS — US, reader",
    "en_US-amy-medium": "Amy — US",
    "en_GB-alba-medium": "Alba — Scottish",
    "en_GB-jenny_dioco-medium": "Jenny — British",
    "en_GB-cori-high": "Cori — British, storyteller",
}
_piper: dict[str, object] = {}


def _piper_voices() -> dict[str, Path]:
    folder = VOICE_PATH.parent
    return {p.stem: p for p in sorted(folder.glob("*.onnx")) if p.with_suffix(".onnx.json").exists()}


def _get_piper(voice_id: str):
    if voice_id not in _piper:
        from piper import PiperVoice

        _piper[voice_id] = PiperVoice.load(str(_piper_voices()[voice_id]))
    return _piper[voice_id]


def _mac_say(text: str, voice: str) -> tuple[int, np.ndarray]:
    """The Mac's built-in speech, made in a few hundred milliseconds."""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "say.wav")
        subprocess.run(
            ["say", "-v", voice, "-o", out, "--file-format=WAVE", "--data-format=LEI16@22050", text],
            check=True, timeout=60,
        )
        with wave.open(out, "rb") as wf:
            return wf.getframerate(), np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)


# Pocket TTS (Kyutai): a natural voice made on the processor, about eight
# times faster than the speech it makes on this Mac.
POCKET_VOICES = [
    ("alba", "Alba — clear"), ("fantine", "Fantine — warm"), ("cosette", "Cosette — bright"),
    ("eponine", "Eponine — lively"), ("azelma", "Azelma — soft"),
    ("marius", "Marius — friendly"), ("jean", "Jean — calm"), ("javert", "Javert — deep"),
]
_pocket = None
_pocket_states: dict[str, object] = {}


def _pocket_ready() -> bool:
    return _importable("pocket_tts")


def _pocket_say(text: str, voice: str) -> tuple[int, np.ndarray]:
    global _pocket
    if _pocket is None:
        from pocket_tts import TTSModel

        _pocket = TTSModel.load_model()
    if voice not in _pocket_states:
        _pocket_states[voice] = _pocket.get_state_for_audio_prompt(voice)
    audio = _pocket.generate_audio(_pocket_states[voice], text).numpy().reshape(-1)
    return int(_pocket.sample_rate), (np.clip(audio, -1, 1) * 32767).astype(np.int16)


# Supertonic 3 (Supertone): about as quick as Kokoro, a different sound.
SUPERTONIC_VOICES = [(f"F{i}", f"Female {i}") for i in range(1, 6)] + [(f"M{i}", f"Male {i}") for i in range(1, 6)]
_supertonic = None
_supertonic_styles: dict[str, object] = {}


def _supertonic_ready() -> bool:
    return _importable("supertonic")


def _supertonic_say(text: str, voice: str, steps: int = 8) -> tuple[int, np.ndarray]:
    global _supertonic
    if _supertonic is None:
        from supertonic import TTS

        _supertonic = TTS()
    if voice not in _supertonic_styles:
        _supertonic_styles[voice] = _supertonic.get_voice_style(voice)
    wav, _ = _supertonic.synthesize(text, voice_style=_supertonic_styles[voice], total_steps=int(steps), lang="en")
    audio = np.asarray(wav, dtype=np.float32).reshape(-1)
    return int(getattr(_supertonic, "sample_rate", 44100)), (np.clip(audio, -1, 1) * 32767).astype(np.int16)


# Whisper sizes for listening (id the Moo sends, model, label).
LISTEN_MODELS = [
    ("whisper-tiny", "mlx-community/whisper-tiny.en-mlx", "Tiny — instant, misses more words"),
    ("whisper-base", "mlx-community/whisper-base.en-mlx", "Base — very fast"),
    ("whisper-small", "mlx-community/whisper-small.en-mlx", "Small — fast and good"),
    ("whisper-turbo", "mlx-community/whisper-large-v3-turbo", "Large turbo — best"),
]

# Story models from the lowest to the highest score in the test of
# 20 November 2026: 74 tries of Moo's own jobs in two rounds (story length,
# no questions, safety, using the child's answer, checking answers, picking
# a word, riddle guesses, writing riddles). The time is for one story part.
_STORY_ORDER = [
    ("qwen2.5:0.5b", "Qwen 2.5 0.5B — score 48%, 0.5 s"),
    ("llama3.2:1b", "Llama 3.2 1B — score 57%, 1.2 s"),
    ("gemma3:1b", "Gemma 3 1B — score 63%, 0.7 s"),
    ("qwen2.5:1.5b", "Qwen 2.5 1.5B — score 68%, 0.7 s"),
    ("phi4-mini", "Phi 4 mini — score 71%, 2.2 s"),
    ("qwen2.5:3b", "Qwen 2.5 3B — score 82%, 0.8 s"),
    ("mistral-nemo", "Mistral Nemo 12B — score 86%, 3.6 s"),
    ("llama3.2:3b", "Llama 3.2 3B — score 89%, 1.5 s"),
    ("llama3.1:8b", "Llama 3.1 8B — score 90%, 3.1 s"),
    ("gemma3:12b", "Gemma 3 12B — score 91%, 4.2 s"),
    ("qwen2.5:7b", "Qwen 2.5 7B — score 92%, 2.2 s"),
    ("gemma3:4b", "Gemma 3 4B — score 93%, 1.6 s"),
]

# One choice that sets everything, from instant to the slowest and richest.
# "high" is what the Moo used on 20 November 2026; "top" has the Qwen3 voice
# (Orpheus until 21 November, when the maker judged it one of the worst).
TIERS = [
    # The story model of each tier is the best scorer at that speed.
    {"id": "instant", "label": "Instant — under a second a part, simpler stories",
     "story_model": "qwen2.5:3b", "listen_model": "whisper-tiny", "voice_engine": "kitten2s", "voice": "Eleanor"},
    {"id": "fast", "label": "Fast — about 2 s, the best scoring story model",
     "story_model": "gemma3:4b", "listen_model": "whisper-base", "voice_engine": "pocket", "voice": "alba"},
    {"id": "balanced", "label": "Balanced — about 2 s, a larger story model",
     "story_model": "qwen2.5:7b", "listen_model": "whisper-small", "voice_engine": "pocket", "voice": "alba"},
    {"id": "high", "label": "High — the largest story model and the Kokoro voice",
     "story_model": "gemma3:12b", "listen_model": "whisper-turbo", "voice_engine": "kokoro", "voice": "af_heart"},
    {"id": "top", "label": "Top — the largest story model and the enthusiastic Qwen3 voice",
     "story_model": "gemma3:12b", "listen_model": "whisper-turbo", "voice_engine": "qwen3", "voice": "serena"},
]


def _listen_ready(repo: str) -> bool:
    cache = Path.home() / ".cache" / "huggingface" / "hub" / ("models--" + repo.replace("/", "--"))
    return cache.exists()


@app.get("/moo/options")
async def options() -> dict:
    """Story models installed here, voice engines and voices, for the Moo's screen."""
    installed = set(await _installed())

    def name(n: str) -> str | None:  # Ollama keeps "phi4-mini" as "phi4-mini:latest"
        return n if n in installed else (f"{n}:latest" if f"{n}:latest" in installed else None)

    story = [{"id": name(n), "label": l} for n, l in _STORY_ORDER if name(n)]
    known = {name(n) for n, _ in _STORY_ORDER}
    story += [{"id": n, "label": n} for n in sorted(installed - known) if "coder" not in n and "embed" not in n]
    engines = [{"id": "mac", "label": "Mac voices — instant, plain"}]
    voices_by_engine: dict[str, list] = {"mac": [{"id": v, "label": l} for v, l in MAC_VOICES]}
    piper = _piper_voices()
    if piper:
        engines.append({"id": "piper", "label": "Piper — very fast, clear"})
        voices_by_engine["piper"] = [
            {"id": v, "label": f"{_PIPER_LABELS.get(v, v)} · {v.rsplit('-', 1)[-1]} quality"} for v in piper
        ]
    if _pocket_ready():
        engines.append({"id": "pocket", "label": "Pocket — natural and very fast"})
        voices_by_engine["pocket"] = [{"id": v, "label": l} for v, l in POCKET_VOICES]
    if _supertonic_ready():
        engines.append({"id": "supertonic", "label": "Supertonic — natural, quick"})
        voices_by_engine["supertonic"] = [{"id": v, "label": l} for v, l in SUPERTONIC_VOICES]
    if _kitten2s_ready():
        engines.insert(1, {"id": "kitten2s", "label": "Kitten 2 small — tiny and the fastest voice"})
        voices_by_engine["kitten2s"] = [{"id": v, "label": l} for v, l in KITTEN2S_VOICES]
    if _kokoro_ready():
        engines.append({"id": "kokoro", "label": "Kokoro — natural, quick"})
        voices_by_engine["kokoro"] = [{"id": v, "label": l} for v, l in KOKORO_VOICES]
    if _kitten2_ready():
        engines.append({"id": "kitten2", "label": "Kitten 2 — story voices, about half the speech to make"})
        voices_by_engine["kitten2"] = [{"id": v, "label": l} for v, l in KITTEN2_VOICES]
    if _qwen3_ready():
        engines.append({"id": "qwen3", "label": "Qwen3 — read as a storyteller, slower"})
        voices_by_engine["qwen3"] = [{"id": v, "label": l} for v, l in QWEN3_VOICES]
    if _orpheus_ready():
        engines.append({"id": "orpheus", "label": "Orpheus — acted, giggles and gasps, slow"})
        voices_by_engine["orpheus"] = [{"id": v, "label": l} for v, l in ORPHEUS_VOICES]
    if _chatterbox_ready():
        engines.append({"id": "chatterbox", "label": "Chatterbox — acted, very slow (20–60 s a sentence)"})
    engine_ids = {e["id"] for e in engines}
    listen = [{"id": k, "label": l} for k, r, l in LISTEN_MODELS if _listen_ready(r)]
    listen_ids = {m["id"] for m in listen}
    tiers = [
        {"id": t["id"], "label": t["label"],
         "settings": {k: t[k] for k in ("story_model", "listen_model", "voice_engine", "voice")}}
        for t in TIERS
        if name(t["story_model"]) and t["voice_engine"] in engine_ids and t["listen_model"] in listen_ids
    ]
    return {
        "tiers": tiers,
        "story_models": story,
        "default_story_model": await _model(),
        "listen_models": listen,
        "default_listen_model": "whisper-turbo",
        "voice_engines": engines,
        "voices_by_engine": voices_by_engine,
        "voice_qualities": {
            e: [{"id": q[0], "label": q[1]} for q in _qualities(e)]
            for e in VOICE_QUALITIES if e in engine_ids and len(_qualities(e)) > 1
        },
        "default_voice_quality": _DEFAULT_QUALITY,
        # older screens
        "voices": voices_by_engine.get("kokoro", []),
        "orpheus_voices": voices_by_engine.get("orpheus", []),
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
