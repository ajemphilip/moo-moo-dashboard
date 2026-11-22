"""Word games on their own, without a story.

POST /games/start  — {"game": "spell" | "name" | "build" | "mix"} start a session
GET  /games/state  — current game, word and progress (phone polls)
POST /games/stop   — end the session

The games are the same three the story plays (setup_service/word_games.py).
Here Moo picks a simple word for each round, plays the chosen game (or a mix),
then asks whether to play another. "The end", "stop" or silence twice ends it.
"""

from __future__ import annotations

import logging
import re
import threading
import time

from fastapi import APIRouter, HTTPException

import config as moo_config

from .. import word_games
from ..models import GameStartRequest, GameState, SimpleStatus
from ..services import braille_hw, service_control

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/games")

_OWNER = "games"
_lock = threading.Lock()
_state: dict = {
    "active": False,
    "dry_run": False,
    "game": None,
    "phase": "idle",
    "message": None,
    "rounds": 0,
    "spelling_word": None,
    "error": None,
    **word_games.GAME_FIELDS,
}
_stop = threading.Event()
_worker: threading.Thread | None = None
_was_running = False

_END_WORDS = {"stop", "finish", "finished", "done", "enough", "bye", "goodbye", "no"}


def _set(**kw) -> None:
    with _lock:
        _state.update(kw)


def _snapshot() -> GameState:
    with _lock:
        return GameState(**_state)


def _wants_end(heard: str) -> bool:
    words = re.findall(r"[a-z]+", heard.lower())
    text = " ".join(words)
    return text.startswith("the end") or (0 < len(words) <= 4 and bool(set(words) & _END_WORDS))


def _run(ctx: word_games.Session, game: str) -> None:
    try:
        intro = {
            "spell": "Let's spell some words together!",
            "name": "Let's read some letters! I raise a letter, you tell me which one it is.",
            "build": "Let's play riddles! Guess the word, then make its first letter.",
            "mix": "Let's play some word games!",
        }[game]
        _set(phase="telling", message=intro)
        ctx.audio.speak(intro)
        rounds = 0
        silent = 0
        while not _stop.is_set():
            _set(phase="thinking", message="Moo is choosing a word…")
            word = word_games.practice_word(ctx)
            this_game = word_games.pick_game(ctx) if game == "mix" else game
            _set(game=this_game if game != "mix" else "mix")
            word_games.play(ctx, word, this_game)
            if _stop.is_set():
                break
            rounds += 1
            _set(rounds=rounds)

            question = "Shall we play another one? Say yes, or say the end."
            _set(phase="quiz", message=question)
            ctx.audio.speak(question)
            _set(phase="listening", message=question)
            heard = word_games.hear(ctx)
            logger.info("games: another? heard %r", heard)
            if _wants_end(heard):
                break
            if not heard:
                silent += 1
                if silent >= 2:
                    break
                continue
            silent = 0
        if not _stop.is_set():
            ctx.audio.speak("That was fun! Goodbye for now.")
        _set(phase="done", message="All done — great playing!", **word_games.GAME_FIELDS)
    except Exception as exc:  # noqa: BLE001
        logger.exception("games session failed")
        _set(phase="error", error=str(exc))


# One start at a time. The phone app once sent two starts in the same
# second, both got past the check of "active" while the first was still being
# set up, and two sessions ran at once: the intro played twice and one
# session's microphone recorded the other's voice (21 November 2026).
_games_start_lock = threading.Lock()


@router.post("/start", response_model=GameState)
def games_start(req: GameStartRequest) -> GameState:
    if not _games_start_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Moo is already starting one")
    try:
        return _games_start_unguarded(req)
    finally:
        _games_start_lock.release()


def _games_start_unguarded(req: GameStartRequest) -> GameState:
    global _worker, _was_running
    with _lock:
        if _state["active"]:
            raise HTTPException(status_code=409, detail="A game is already running")

    cfg = moo_config.load_config()
    from assistant.ai_client import ai_ready, make_client
    import assistant.story as story_text

    _ai = cfg.get("ai") or {}
    story_text.EXPRESSIVE_CUES = _ai.get("mode") == "local" and _ai.get("voice_engine") == "orpheus"

    if not ai_ready(cfg):
        raise HTTPException(
            status_code=400,
            detail="No AI set up yet. Add an OpenAI key or choose Moo Local under AI engine.",
        )
    try:
        import openai
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="openai package not installed") from exc

    _was_running = service_control.is_assistant_running()
    if _was_running:
        service_control.stop_assistant()
        time.sleep(1.0)

    client = make_client(cfg)
    prefs = cfg.get("preferences", {}) or {}
    audio_cfg = cfg.get("audio", {}) or {}
    from assistant.audio import AudioIO

    audio = AudioIO(
        client,
        tts_voice=prefs.get("tts_voice", "sage"),
        input_device=audio_cfg.get("input_index"),
        output_device=audio_cfg.get("output_index"),
    )
    try:
        driver = braille_hw.acquire(_OWNER)
    except RuntimeError as exc:
        if _was_running:
            service_control.start_assistant()
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    _stop.clear()
    with _lock:
        _state.update(
            active=True,
            dry_run=bool(getattr(driver, "dry_run", False)),
            game=req.game,
            phase="idle",
            message="Starting…",
            rounds=0,
            spelling_word=None,
            error=None,
            **word_games.GAME_FIELDS,
        )
    ctx = word_games.Session(
        client=client, audio=audio, age_range=prefs.get("age_range") or "3-5",
        set=_set, stop=_stop, owner=_OWNER,
    )
    _worker = threading.Thread(target=_run, args=(ctx, req.game), daemon=True)
    _worker.start()
    return _snapshot()


@router.get("/state", response_model=GameState)
def games_state() -> GameState:
    return _snapshot()


@router.post("/stop", response_model=SimpleStatus)
def games_stop() -> SimpleStatus:
    global _worker
    _stop.set()
    if _worker is not None:
        _worker.join(timeout=8.0)
        _worker = None
    braille_hw.release(_OWNER)
    with _lock:
        _state.update(active=False, phase="idle", message=None)
    if _was_running:
        service_control.start_assistant()
    return SimpleStatus(status="stopped")
