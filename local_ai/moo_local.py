"""Moo Local — speech, story and voice for the Moo, computed on this computer.

The Moo normally calls OpenAI. In local mode it calls this server instead, on
the home network, with exactly the same requests. This server answers the
four kinds the Moo uses, in OpenAI's format, so the Pi code only changes the
address it talks to:

  POST /v1/chat/completions      story, games, checks   -> Ollama (gemma3:12b)
  POST /v1/audio/transcriptions  the child's speech     -> Whisper (mlx-whisper on Apple silicon,
                                                           faster-whisper everywhere else)
  POST /v1/audio/speech          Moo's voice            -> Kokoro (kokoro-onnx), Piper as fallback
  POST /v1/moderations           safety check           -> Ollama, yes or no
  GET  /health                   what is running here

It announces itself on the network as `_mooai._tcp`, so the Moo's setup page
finds it without anyone typing an address.

It runs on macOS, Windows and Linux. The voices that need Apple's MLX
(Orpheus, Qwen3-TTS, the full Kitten TTS 2) and the Mac's own voices are
offered only on a Mac with Apple silicon; the voice list it reports names
only what works on this computer.

Run:      ~/.moo-local-venv/bin/python moo_local.py
Prepare:  ~/.moo-local-venv/bin/python moo_local.py --prepare
          (downloads the listening models and voices, then exits)
"""

from __future__ import annotations

import io
import json
import logging
import os
import platform
import re
import socket
import sys
import threading
import time
import wave
from pathlib import Path

import httpx
import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

# Started at login there is no window: everything goes to the log file.
# (On Windows pythonw.exe has no output at all without this.)
if "--log" in sys.argv:
    _log_file = open(sys.argv[sys.argv.index("--log") + 1], "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stderr = _log_file
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
log = logging.getLogger("moo-local")

# Apple silicon gets the MLX engines; every other computer the portable ones.
APPLE = sys.platform == "darwin" and platform.machine() == "arm64" and not os.environ.get("MOO_LOCAL_PORTABLE")
WINDOWS = os.name == "nt"

PORT = int(os.environ.get("MOO_LOCAL_PORT", "8765"))
OLLAMA = os.environ.get("MOO_OLLAMA_URL", "http://127.0.0.1:11434")
CHAT_MODEL = os.environ.get("MOO_LOCAL_CHAT_MODEL", "gemma3:12b")
FALLBACK_CHAT_MODEL = "llama3.2:3b"
WHISPER_MODEL = os.environ.get(
    "MOO_LOCAL_WHISPER",
    "mlx-community/whisper-large-v3-turbo" if APPLE else "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
)
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
    else Gemma 3 12B, else the best scoring model installed, else Llama 3.2 3B."""
    global _chat_model
    names = await _installed()
    if requested and requested in names:
        return requested
    if not _chat_model or (names and _chat_model not in names):
        def have(n: str) -> str | None:  # Ollama keeps "phi4-mini" as "phi4-mini:latest"
            return n if n in names else (f"{n}:latest" if f"{n}:latest" in names else None)

        best = [have(n) for n, _ in _STORY_ORDER if have(n)]
        _chat_model = have(CHAT_MODEL) or (best[-1] if best else FALLBACK_CHAT_MODEL)
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
    if body["model"] not in await _installed():
        raise MooError(409, "No story model is installed on this computer. Choose one under Models on the AI engine screen.")
    _refuse_if_too_big(f"story:{_bare(body['model'])}")
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
    audio = _wav_to_float16k(file.file.read())
    if audio.size < 1600:  # under 0.1 s
        return JSONResponse({"text": ""})
    started = time.time()
    repo = _listen_repo(model)
    if not _cached(repo):
        raise MooError(409, "No listening model is installed on this computer. Choose one under Models on the AI engine screen.")
    _refuse_if_too_big("listen:" + next((k for k, r, _ in LISTEN_MODELS if r == repo), "whisper-turbo"))
    with _whisper_lock:
        text = _transcribe(audio, repo, language)
    log.info("heard in %.1fs with %s: %r", time.time() - started, repo.split("/")[-1], text)
    return JSONResponse({"text": text})


def _listen_repo(model_id: str) -> str:
    """The Whisper model behind the id the Moo sends. An id it does not know
    ("whisper-1" from older screens) gets the largest one downloaded here."""
    repos = {k: r for k, r, _ in LISTEN_MODELS}
    if model_id in repos:
        return repos[model_id]
    cached = [r for _, r, _ in LISTEN_MODELS if _cached(r)]
    return WHISPER_MODEL if WHISPER_MODEL in cached or not cached else cached[-1]


_fw: dict[str, object] = {}  # one faster-whisper model in memory
_fw_cuda = True  # cleared when the graphics card fails once


def _faster_whisper(repo: str, cuda: bool):
    from faster_whisper import WhisperModel

    key = f"{repo}|{'cuda' if cuda else 'cpu'}"
    if key not in _fw:
        _fw.clear()
        if cuda:
            _fw[key] = WhisperModel(repo, device="cuda", compute_type="float16")
        else:
            _fw[key] = WhisperModel(repo, device="cpu", compute_type="int8")
        log.info("listening with %s on the %s", repo.split("/")[-1], "graphics card" if cuda else "processor")
    return _fw[key]


def _cuda_present() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _transcribe(audio: np.ndarray, repo: str, language: str | None) -> str:
    """Speech to text: mlx-whisper on Apple silicon, faster-whisper elsewhere
    (an NVIDIA card when it works, the processor otherwise)."""
    global _fw_cuda
    if repo.startswith("mlx-community/"):
        import mlx_whisper

        result = mlx_whisper.transcribe(audio, path_or_hf_repo=repo)
        return (result.get("text") or "").strip()
    lang = language or ("en" if repo.endswith(".en") else None)
    if _fw_cuda and _cuda_present():
        try:
            segments, _ = _faster_whisper(repo, True).transcribe(audio, language=lang)
            return " ".join(s.text.strip() for s in segments).strip()
        except Exception:
            # Usually missing CUDA libraries. The processor is slower but always works.
            log.warning("listening on the graphics card failed; using the processor from now on", exc_info=True)
            _fw_cuda = False
            _fw.clear()
    segments, _ = _faster_whisper(repo, False).transcribe(audio, language=lang)
    return " ".join(s.text.strip() for s in segments).strip()


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
    if engine == "mac" and not APPLE:
        engine = "kokoro"
    if engine not in ("mac", "chatterbox") and not _engine_ready(engine):
        ready = [e for e in _ENGINE_ORDER if _engine_ready(e)]
        if ready:  # the chosen voice model was replaced by another; speak with that one
            engine, voice_id, quality = ready[0], "", None
    if engine in ("orpheus", "qwen3", "kitten2") and not APPLE:
        engine = "kitten2s" if _kitten2s_ready() else "kokoro"
        voice_id = ""
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
        if engine in ("orpheus", "kitten2") or (engine == "qwen3" and _qwen3_ready()):
            _refuse_if_too_big(f"voice:{engine}" + (f":{_quality_id('qwen3', quality)}" if engine == "qwen3" else ""))
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
        elif VOICE_PATH.exists() and VOICE_PATH.with_suffix(".onnx.json").exists():
            voice = _get_voice()
            with wave.open(buf, "wb") as wf:
                voice.synthesize_wav(text, wf)
        elif _piper_voices():
            with wave.open(buf, "wb") as wf:
                _get_piper(next(iter(_piper_voices()))).synthesize_wav(text, wf)
        else:
            raise MooError(409, "No voice is installed on this computer. Choose one under Models on the AI engine screen.")
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



_ENGINE_ORDER = ["kitten2s", "kokoro", "pocket", "supertonic", "piper", "qwen3", "kitten2", "orpheus", "mac"]


def _engine_ready(engine: str) -> bool:
    return {
        "kitten2s": _kitten2s_ready, "kokoro": _kokoro_ready, "pocket": _pocket_ready,
        "supertonic": _supertonic_ready, "piper": lambda: bool(_piper_voices()),
        "qwen3": _qwen3_ready, "kitten2": _kitten2_ready, "orpheus": _orpheus_ready,
        "mac": lambda: APPLE,
    }.get(engine, _kokoro_ready)()


def _quiet() -> dict:
    """Popen options for a helper process: its messages go to our log, and
    on Windows it opens no console window."""
    kw: dict = {}
    try:
        sys.stderr.fileno()
        kw["stderr"] = sys.stderr
    except Exception:
        kw["stderr"] = __import__("subprocess").DEVNULL
    if WINDOWS:
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    return kw


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
    return _mlx_audio() and _cached(ORPHEUS_MODEL)


def _mlx_audio() -> bool:
    return APPLE and _importable("mlx_audio.tts.utils")


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
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1, **_quiet(),
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
KITTEN2_PYTHON = Path(os.environ.get(
    "MOO_LOCAL_KITTEN2_PYTHON",
    str(Path.home() / ".moo-kitten2-venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")),
))
_kitten2 = None  # the worker process


def _kitten2_ready() -> bool:
    # The full model runs through MLX, so only on Apple silicon.
    return APPLE and KITTEN2_PYTHON.exists() and _cached("KittenML/kitten-tts-2")


def _kitten2s_ready() -> bool:
    return KITTEN2_PYTHON.exists() and any(_cached(q[2]) for q in VOICE_QUALITIES["kitten2s"])


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
            encoding="utf-8", **_quiet(),
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


def _hub_cache() -> Path:
    try:
        from huggingface_hub import constants

        return Path(constants.HF_HUB_CACHE)
    except Exception:
        return Path.home() / ".cache" / "huggingface" / "hub"


def _cached(repo: str) -> bool:
    """Whether a Hugging Face model is downloaded (its snapshot, not just a folder)."""
    snaps = _hub_cache() / ("models--" + repo.replace("/", "--")) / "snapshots"
    return snaps.is_dir() and any(snaps.iterdir())


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


def _quality_id(engine: str, wanted: str | None) -> str:
    """The id of the quality `_quality` picks."""
    have = [q[0] for q in _qualities(engine)]
    return next((q for q in (wanted, _DEFAULT_QUALITY.get(engine)) if q in have), have[0] if have else "")


def _quality(engine: str, wanted: str | None):
    """The setting behind a quality id: a model for Kitten and Qwen3, a
    number of steps for Supertonic. Falls back to the default."""
    have = {q[0]: q[2] for q in _qualities(engine)}
    return have.get(wanted or "") or have.get(_DEFAULT_QUALITY.get(engine, "")) or next(iter(have.values()), None)


def _qwen3_ready() -> bool:
    return _mlx_audio() and any(_cached(q[2]) for q in VOICE_QUALITIES["qwen3"])


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


POCKET_REPO = "kyutai/pocket-tts"


def _pocket_ready() -> bool:
    return _cached(POCKET_REPO) and _importable("pocket_tts")


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


SUPERTONIC_DIR = Path.home() / ".cache" / "supertonic3"


def _supertonic_ready() -> bool:
    return SUPERTONIC_DIR.is_dir() and any(SUPERTONIC_DIR.iterdir()) and _importable("supertonic")


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


# Whisper sizes for listening (id the Moo sends, model, label). The same
# sizes in MLX on Apple silicon and for faster-whisper everywhere else.
LISTEN_MODELS = [
    ("whisper-tiny", "mlx-community/whisper-tiny.en-mlx", "Tiny — instant, misses more words"),
    ("whisper-base", "mlx-community/whisper-base.en-mlx", "Base — very fast"),
    ("whisper-small", "mlx-community/whisper-small.en-mlx", "Small — fast and good"),
    ("whisper-turbo", "mlx-community/whisper-large-v3-turbo", "Large turbo — best"),
] if APPLE else [
    ("whisper-tiny", "Systran/faster-whisper-tiny.en", "Tiny — instant, misses more words"),
    ("whisper-base", "Systran/faster-whisper-base.en", "Base — very fast"),
    ("whisper-small", "Systran/faster-whisper-small.en", "Small — fast and good"),
    ("whisper-turbo", "mobiuslabsgmbh/faster-whisper-large-v3-turbo", "Large turbo — best"),
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
    return _cached(repo)


@app.get("/moo/options")
async def options() -> dict:
    """Story models installed here, voice engines and voices, for the Moo's screen."""
    installed = set(await _installed())

    def name(n: str) -> str | None:  # Ollama keeps "phi4-mini" as "phi4-mini:latest"
        return n if n in installed else (f"{n}:latest" if f"{n}:latest" in installed else None)

    story = [{"id": name(n), "label": l} for n, l in _STORY_ORDER if name(n)]
    known = {name(n) for n, _ in _STORY_ORDER}
    story += [{"id": n, "label": n} for n in sorted(installed - known) if "coder" not in n and "embed" not in n]
    engines: list[dict] = []
    voices_by_engine: dict[str, list] = {}
    if APPLE:  # the Mac's own `say` voices
        engines.append({"id": "mac", "label": "Mac voices — instant, plain"})
        voices_by_engine["mac"] = [{"id": v, "label": l} for v, l in MAC_VOICES]
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
        engines.insert(1 if APPLE else 0, {"id": "kitten2s", "label": "Kitten 2 small — tiny and the fastest voice"})
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
        "default_listen_model": "whisper-turbo" if "whisper-turbo" in listen_ids else (
            listen[-1]["id"] if listen else "whisper-turbo"),
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



# --- models: what can be installed here, and installing it --------------------
#
# Nothing is downloaded until the parent chooses it on the AI engine screen.
# Each model has an id, its download size and the memory it needs while it
# runs; one that does not fit this computer is refused with a clear error
# that names a smaller one of the same kind.

class MooError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


@app.exception_handler(MooError)
async def _moo_error(_request: Request, exc: MooError) -> JSONResponse:
    # "detail" for the Moo's setup app, "error" in OpenAI's shape for the storyteller.
    return JSONResponse(status_code=exc.status, content={
        "detail": exc.message, "error": {"message": exc.message, "type": "moo_local"}})


# Download size of each story model in MB (Ollama's default quantization).
_STORY_MB = {
    "qwen2.5:0.5b": 400, "llama3.2:1b": 1300, "gemma3:1b": 815, "qwen2.5:1.5b": 990,
    "phi4-mini": 2500, "qwen2.5:3b": 1900, "mistral-nemo": 7100, "llama3.2:3b": 2000,
    "llama3.1:8b": 4900, "gemma3:12b": 8100, "qwen2.5:7b": 4700, "gemma3:4b": 3300,
}
_LISTEN_MB = {"whisper-tiny": 75, "whisper-base": 145, "whisper-small": 485, "whisper-turbo": 1600}
_PIPER_MB = {"low": 25, "medium": 63, "high": 115}


def _bare(name: str) -> str:
    return name[: -len(":latest")] if name.endswith(":latest") else name


def _memory_mb() -> int:
    """This computer's memory. MOO_LOCAL_RAM_MB pretends another size (for tests)."""
    if os.environ.get("MOO_LOCAL_RAM_MB"):
        return int(os.environ["MOO_LOCAL_RAM_MB"])
    try:
        if WINDOWS:
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = Status()
            st.dwLength = ctypes.sizeof(Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return int(st.ullTotalPhys // 2**20)
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // 2**20)
    except Exception:
        return 8192


def _usable_mb() -> int:
    """What one model may use: the system, the browser and the other models
    need the rest."""
    return int(_memory_mb() * 0.7)


def _disk_free_mb() -> int:
    try:
        import shutil

        return int(shutil.disk_usage(Path.home()).free // 2**20)
    except Exception:
        return 0


def _needs_mb(size_mb: int, story: bool = False) -> int:
    # A story model also holds its context; the others little more than their weights.
    return int(size_mb * 1.25 + 500) if story else int(size_mb * 1.3 + 200)


def _catalogue() -> list[dict]:
    """Every model this computer could run, smallest first within its kind."""
    items: list[dict] = []
    for name, label in sorted(_STORY_ORDER, key=lambda x: _STORY_MB.get(x[0], 0)):
        mb = _STORY_MB.get(name, 0)
        items.append({"id": f"story:{name}", "kind": "story", "family": "story", "label": label,
                      "size_mb": mb, "needs_mb": _needs_mb(mb, True)})
    for key, repo, label in LISTEN_MODELS:
        mb = _LISTEN_MB.get(key, 0)
        items.append({"id": f"listen:{key}", "kind": "listen", "family": "listen", "label": label,
                      "size_mb": mb, "needs_mb": _needs_mb(mb), "repo": repo})

    def voice(vid: str, family: str, label: str, mb: int, **kw) -> None:
        items.append({"id": f"voice:{vid}", "kind": "voice", "family": family, "label": label,
                      "size_mb": mb, "needs_mb": _needs_mb(mb) if mb else 0, **kw})

    if APPLE:  # built into macOS: nothing to download, always there
        voice("mac", "mac", "Mac voices — instant, plain, built in", 0, builtin=True)
    if KITTEN2_PYTHON.exists():
        for (q, qlabel, repo), mb in zip(VOICE_QUALITIES["kitten2s"], (25, 60, 330)):
            voice(f"kitten2s:{q}", "kitten2s", f"Kitten 2 small, {qlabel}", mb, repo=repo)
    voice("kokoro", "kokoro", "Kokoro — natural, quick, ten voices", 340)
    if _importable("supertonic"):
        voice("supertonic", "supertonic", "Supertonic — natural, quick, ten voices", 260)
    if _importable("pocket_tts"):
        voice("pocket", "pocket", "Pocket — natural and very fast, eight voices", 450, repo=POCKET_REPO)
    if _importable("piper"):
        for vid, label in _PIPER_LABELS.items():
            voice(f"piper:{vid}", "piper", f"Piper, {label}", _PIPER_MB.get(vid.rsplit("-", 1)[-1], 63))
    if APPLE and KITTEN2_PYTHON.exists():
        voice("kitten2", "kitten2", "Kitten 2 — story voices, 1.7B", 3500, repo="KittenML/kitten-tts-2")
    if _mlx_audio():
        for (q, qlabel, repo), mb in zip(VOICE_QUALITIES["qwen3"], (1000, 2300)):
            voice(f"qwen3:{q}", "qwen3", f"Qwen3, {qlabel}", mb, repo=repo)
        voice("orpheus", "orpheus", "Orpheus — acted, giggles and gasps, slow", 2300, repo=ORPHEUS_MODEL)
    return items


def _item(model_id: str) -> dict:
    for it in _catalogue():
        if it["id"] == model_id:
            return it
    raise MooError(404, f"{model_id} is not a model this computer can use")


def _too_big(it: dict) -> str | None:
    """Why a model does not fit this computer, with a smaller one to use instead."""
    if it["needs_mb"] <= _usable_mb():
        return None
    gb = round(_memory_mb() / 1024)
    smaller = [o for o in _catalogue() if o["family"] == it["family"] and o["needs_mb"] <= _usable_mb()
               and o["size_mb"] < it["size_mb"]]
    if it["kind"] == "story":  # the best scorer that fits, not only the largest
        order = [f"story:{n}" for n, _ in _STORY_ORDER]
        smaller.sort(key=lambda o: order.index(o["id"]) if o["id"] in order else -1)
    hint = f" Try {smaller[-1]['label'].split(' — ')[0]} instead." if smaller else " Choose a smaller model."
    return (f"{it['label'].split(' — ')[0]} is too big for this computer: it needs about "
            f"{it['needs_mb'] / 1024:.1f} GB of memory and this computer has {gb} GB.{hint}")


def _refuse_if_too_big(model_id: str) -> None:
    try:
        it = _item(model_id)
    except MooError:
        return  # a model of the maker's own, outside the catalogue
    reason = _too_big(it)
    if reason:
        raise MooError(507, reason)


def _installed_ids(story_names: list[str]) -> set[str]:
    have = {f"story:{_bare(n)}" for n in story_names}
    for it in _catalogue():
        if it["kind"] == "story":
            continue
        if it.get("builtin"):
            have.add(it["id"])
            continue
        family, vid = it["family"], it["id"].split(":", 2)[-1]
        if it.get("repo") and family not in ("pocket",):
            ok = _cached(it["repo"])
        elif family == "pocket":
            ok = _cached(POCKET_REPO)
        elif family == "kokoro":
            ok = _kokoro_ready()
        elif family == "supertonic":
            ok = SUPERTONIC_DIR.is_dir() and any(SUPERTONIC_DIR.iterdir())
        elif family == "piper":
            ok = vid in _piper_voices()
        else:
            ok = False
        if ok:
            have.add(it["id"])
    return have


_job: dict = {}  # the one download running or last finished
_job_lock = threading.Lock()


def _folder_mb(path: Path) -> int:
    try:
        return int(sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) // 2**20)
    except Exception:
        return 0


def _download(url: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        with open(part, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                _job["done_mb"] = _job.get("base_mb", 0) + f.tell() // 2**20
    part.replace(out)


def _piper_url(vid: str) -> str:
    lang, speaker, q = vid.split("-", 2)
    return f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{lang.split('_')[0]}/{lang}/{speaker}/{q}/{vid}"


def _install(it: dict) -> None:
    family, vid = it["family"], it["id"].split(":", 1)[1]
    watch: Path | None = None
    if it.get("repo"):
        watch = _hub_cache() / ("models--" + it["repo"].replace("/", "--"))
    elif family == "supertonic":
        watch = SUPERTONIC_DIR
    stop = threading.Event()

    def progress() -> None:  # Hugging Face and the voice libraries report nothing; watch the folder grow
        while watch and not stop.wait(1):
            _job["done_mb"] = min(_folder_mb(watch), it["size_mb"])

    threading.Thread(target=progress, daemon=True).start()
    try:
        if it["kind"] == "story":
            with httpx.stream("POST", f"{OLLAMA}/api/pull", json={"model": vid, "stream": True}, timeout=None) as r:
                layers: dict[str, tuple[int, int]] = {}
                for line in r.iter_lines():
                    if not line:
                        continue
                    msg = json.loads(line)
                    if msg.get("error"):
                        raise RuntimeError(msg["error"])
                    if msg.get("digest") and msg.get("total"):
                        layers[msg["digest"]] = (msg.get("completed") or 0, msg["total"])
                        _job["done_mb"] = sum(c for c, _ in layers.values()) // 2**20
                        _job["total_mb"] = max(it["size_mb"], sum(t for _, t in layers.values()) // 2**20)
        elif family == "kokoro":
            base = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
            _download(f"{base}/voices-v1.0.bin", KOKORO_DIR / "voices-v1.0.bin")
            _job["base_mb"] = _job["done_mb"]
            _download(f"{base}/kokoro-v1.0.onnx", KOKORO_DIR / "kokoro-v1.0.onnx")
        elif family == "piper":
            folder = VOICE_PATH.parent
            _download(_piper_url(vid) + ".onnx.json", folder / f"{vid}.onnx.json.tmp")
            _download(_piper_url(vid) + ".onnx", folder / f"{vid}.onnx")
            (folder / f"{vid}.onnx.json.tmp").replace(folder / f"{vid}.onnx.json")
        elif family == "pocket":
            with _voice_lock:  # loading it downloads the model, each voice its own small file
                for v, _ in POCKET_VOICES:
                    _pocket_say("Hello.", v)
        elif family == "supertonic":
            with _voice_lock:
                for v, _ in SUPERTONIC_VOICES:
                    _supertonic_say("Hello.", v, 3)
        else:
            from huggingface_hub import snapshot_download

            snapshot_download(it["repo"])
        _ready_cache.clear()
        name = it["label"].split(" — ")[0]
        _job.update(phase="checking", message=f"Checking that {name} works on this computer")
        try:
            _check_loads(it)
        except Exception as exc:
            # It does not run here: take it away again and keep what was there.
            log.warning("%s installed but does not load", it["id"], exc_info=True)
            try:
                _remove(it)
            except Exception:
                pass
            _job.update(status="error", message=f"{name} was downloaded but does not run on this computer ({exc}). "
                                                "The model you had is kept. Choose a smaller one.")
            return
        replaced = []
        for old in _job.get("replace") or []:
            if old != it["id"]:
                try:
                    _remove(_item(old))
                    replaced.append(_item(old)["label"].split(" — ")[0])
                except Exception:
                    log.warning("removing %s after installing %s failed", old, it["id"], exc_info=True)
        _job.update(status="done", done_mb=_job.get("total_mb", it["size_mb"]),
                    message=f"{name} is installed" + (f" in place of {', '.join(replaced)}." if replaced else "."))
        log.info("installed %s, replaced %s", it["id"], replaced)
    except Exception as exc:
        log.warning("installing %s failed", it["id"], exc_info=True)
        _job.update(status="error", message=f"{it['label'].split(' — ')[0]} could not be installed: {exc}")
    finally:
        stop.set()


def _check_loads(it: dict) -> None:
    """Loads a newly installed model once and makes a few words with it."""
    family, vid = it["family"], it["id"].split(":", 1)[1]
    if it["kind"] == "story":
        r = httpx.post(f"{OLLAMA}/api/generate", timeout=600, json={
            "model": vid, "prompt": "Say hello.", "stream": False, "options": {"num_predict": 5}})
        if r.status_code != 200:
            raise RuntimeError(r.json().get("error") if r.headers.get("content-type", "").startswith("application/json") else r.text[:200])
        return
    if it["kind"] == "listen":
        with _whisper_lock:
            _transcribe(np.zeros(16000, dtype=np.float32), it["repo"], None)
        return
    with _voice_lock:
        if family == "kokoro":
            _get_kokoro().create("Hello.", voice=KOKORO_VOICE, speed=1.0, lang="en-us")
        elif family == "piper":
            _get_piper(vid)
        elif family in ("kitten2s", "kitten2"):
            _kitten2_say("Hello.", "Eleanor" if family == "kitten2s" else KITTEN2_VOICES[0][0], it["repo"])
        elif family in ("qwen3", "orpheus"):
            _orpheus_say("Hello.", "", family, it["repo"])
        # Pocket and Supertonic already spoke while they were installed.


def _remove(it: dict) -> None:
    import shutil

    global _kokoro, _pocket, _supertonic, _kitten2
    family, vid = it["family"], it["id"].split(":", 1)[1]
    if it.get("builtin"):
        return  # part of the computer, nothing to delete
    if it["kind"] == "story":
        r = httpx.request("DELETE", f"{OLLAMA}/api/delete", json={"model": vid}, timeout=30)
        if r.status_code == 404:
            httpx.request("DELETE", f"{OLLAMA}/api/delete", json={"model": f"{vid}:latest"}, timeout=30)
        return
    with _voice_lock:
        if family == "kokoro":
            _kokoro = None
            for f in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
                (KOKORO_DIR / f).unlink(missing_ok=True)
        elif family == "piper":
            _piper.pop(vid, None)
            for ext in (".onnx", ".onnx.json"):
                (VOICE_PATH.parent / f"{vid}{ext}").unlink(missing_ok=True)
        elif family == "supertonic":
            _supertonic = None
            _supertonic_styles.clear()
            shutil.rmtree(SUPERTONIC_DIR, ignore_errors=True)
        else:
            if family == "pocket":
                _pocket = None
                _pocket_states.clear()
            if family in ("kitten2s", "kitten2") and _kitten2 is not None:
                _kitten2.kill()
                _kitten2 = None
            if family in ("qwen3", "orpheus") and _orpheus is not None:
                _orpheus.kill()
            _fw.clear()
            shutil.rmtree(_hub_cache() / ("models--" + it["repo"].replace("/", "--")), ignore_errors=True)
    _ready_cache.clear()


@app.get("/moo/models")
async def models() -> dict:
    """The catalogue for the Moo's AI engine screen: each model with its size,
    the memory it needs, whether it is installed, and whether it fits."""
    installed = _installed_ids(await _installed())
    items = []
    for it in _catalogue():
        reason = _too_big(it)
        items.append({k: v for k, v in it.items() if k != "repo"} | {
            "installed": it["id"] in installed, "fits": reason is None, "too_big": reason})
    return {
        "computer": socket.gethostname(),
        "memory_mb": _memory_mb(),
        "usable_mb": _usable_mb(),
        "disk_free_mb": _disk_free_mb(),
        "ollama": bool(await _installed()) or _ollama_up(),
        "models": items,
        "job": dict(_job) or None,
    }


@app.get("/moo/models/job")
def models_job() -> dict:
    return {"job": dict(_job) or None}


@app.post("/moo/models/install")
async def models_install(request: Request) -> dict:
    body = await request.json()
    model_id = str(body.get("id") or "")
    replace = [str(x) for x in (body.get("replace") or []) if str(x) != model_id]
    it = _item(model_id)
    reason = _too_big(it)
    if reason:
        raise MooError(507, reason)
    if _disk_free_mb() < it["size_mb"] + 1024:
        raise MooError(507, f"Not enough free space: {it['label'].split(' — ')[0]} needs about "
                            f"{(it['size_mb'] + 1024) / 1024:.1f} GB and this computer has "
                            f"{_disk_free_mb() / 1024:.1f} GB free.")
    if it["kind"] == "story" and not _ollama_up():
        raise MooError(503, "Ollama is not running on this computer, so story models cannot be installed.")
    with _job_lock:
        if _job.get("status") == "running":
            raise MooError(409, f"Wait until {_job.get('label')} has finished installing.")
        _job.clear()
        _job.update(id=it["id"], kind=it["kind"], label=it["label"].split(" — ")[0], status="running",
                    phase="downloading", done_mb=0, total_mb=it["size_mb"], message="", replace=replace)
    threading.Thread(target=_install, args=(it,), daemon=True).start()
    return {"job": dict(_job)}


@app.post("/moo/models/remove")
async def models_remove(request: Request) -> dict:
    model_id = str((await request.json()).get("id") or "")
    it = _item(model_id)
    if _job.get("status") == "running" and _job.get("id") == model_id:
        raise MooError(409, "It is still installing.")
    _remove(it)
    global _chat_model
    if it["kind"] == "story" and _chat_model and _bare(_chat_model) == model_id.split(":", 1)[1]:
        _chat_model = None
    log.info("removed %s", model_id)
    return {"removed": model_id}

# --- status ------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    names = await _installed()
    ollama_ok = bool(names) and (await _model()) in names
    return {
        "service": "moo-local",
        "version": "0.1.0",
        "computer": socket.gethostname(),
        "system": f"{platform.system()} {platform.machine()}",
        "story_model": _chat_model or CHAT_MODEL,
        "story_model_ready": ollama_ok,
        "speech_model": _listen_repo("whisper-1"),
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


def _ollama_up() -> bool:
    try:
        return httpx.get(f"{OLLAMA}/api/tags", timeout=3).status_code == 200
    except Exception:
        return False


def _start_ollama() -> None:
    """Starts Ollama if it is installed but not running (after a restart, or
    the copy the installer placed in ~/.moo-local/ollama)."""
    import shutil
    import subprocess

    if _ollama_up():
        return
    exe = "ollama.exe" if WINDOWS else "ollama"
    candidates = [
        os.environ.get("MOO_OLLAMA_BIN", ""),
        str(Path.home() / ".moo-local" / "ollama" / exe),
        shutil.which("ollama") or "",
        "/opt/homebrew/bin/ollama", "/usr/local/bin/ollama",
        "/Applications/Ollama.app/Contents/Resources/ollama",
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / exe) if WINDOWS else "",
    ]
    binary = next((c for c in candidates if c and Path(c).is_file()), None)
    if not binary:
        log.warning("Ollama is not installed; stories will not work until it is")
        return
    log.info("starting Ollama: %s", binary)
    subprocess.Popen([binary, "serve"], stdout=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                     start_new_session=not WINDOWS, **_quiet())
    for _ in range(30):
        if _ollama_up():
            return
        time.sleep(1)
    log.warning("Ollama did not answer after 30 s")


def _announce():
    if sys.platform == "darwin":
        return _announce_dns_sd()
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

    def stop() -> None:
        zc.unregister_service(info)
        zc.close()

    return stop


def _announce_dns_sd():
    """On a Mac the system's own mDNS service announces Moo Local. It keeps
    working when Moo Local is started at login, where macOS may not let a
    Python program send to the local network by itself."""
    import subprocess

    host = socket.gethostname().split(".")[0]
    # dns-sd runs until stopped. The small shell around it stops it as soon as
    # this process ends, however it ends, by watching the pipe close.
    proc = subprocess.Popen(
        ["/bin/sh", "-c", 'dns-sd -R "$1" "$2" local "$3" path=/v1 version=0.1.0 >/dev/null & p=$!; '
         'trap "kill $p" EXIT; read _', "sh", f"Moo Local on {host}", SERVICE_TYPE.replace(".local.", ""), str(PORT)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, **_quiet(),
    )
    log.info("announced as 'Moo Local on %s' (%s) on port %d", host, SERVICE_TYPE, PORT)
    return proc.terminate


# --- downloads the installer asks for ---------------------------------------

def prepare() -> int:
    """Downloads the listening models and the voices, so the Moo's screen
    offers them at once. Each one that fails is reported and skipped."""
    from huggingface_hub import snapshot_download

    failed = []

    def step(what: str, fn) -> None:
        print(f"  {what}", flush=True)
        try:
            fn()
        except Exception as exc:
            failed.append(what)
            print(f"    did not work: {exc!r}", flush=True)

    for key, repo, _ in LISTEN_MODELS:
        if key != "whisper-turbo":  # the large one (1.6 GB) only when asked for
            step(f"listening, {key}", lambda r=repo: snapshot_download(r))
    if KITTEN2_PYTHON.exists():
        for _, _, repo in VOICE_QUALITIES["kitten2s"]:
            step(f"Kitten 2 {repo.rsplit('-', 1)[-1]}", lambda r=repo: (snapshot_download(r), _kitten2_say("Hello.", "Eleanor", r)))
    if _importable("pocket_tts"):
        step("Pocket voices", lambda: [_pocket_say("Hello.", v) for v, _ in POCKET_VOICES])
    if _importable("supertonic"):
        step("Supertonic voices", lambda: [_supertonic_say("Hello.", v, 3) for v, _ in SUPERTONIC_VOICES])
    if _kokoro_ready():
        step("Kokoro", lambda: _get_kokoro().create("Hello.", voice=KOKORO_VOICE, speed=1.0, lang="en-us"))
    if _piper_voices():
        step("Piper", lambda: [_get_piper(v) for v in _piper_voices()])
    if _kitten2 is not None:
        _kitten2.kill()
    print("  ready" if not failed else f"  ready, without: {', '.join(failed)}", flush=True)
    return 0


if __name__ == "__main__":
    if "--prepare" in sys.argv:
        sys.exit(prepare())
    threading.Thread(target=_start_ollama, daemon=True).start()
    stop_announcing = _announce()
    try:
        uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
    finally:
        stop_announcing()
