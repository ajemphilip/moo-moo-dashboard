"""Storytelling session endpoints.

POST /story/start    — begin a session: Moo greets, asks for a theme by voice,
                       writes a short story of about 15 s, reads it
                       through, asks what happens next, then spells a noun on
                       the Braille cell.
POST /story/continue — Moo asks what happens next, records the answer, and
                       extends the SAME story coherently, then spells again.
GET  /story/state    — current phase + the latest spoken line (phone polls).
POST /story/stop     — end the session.

The work runs on a background thread; the phone polls /story/state to follow
along through each phase (greeting -> listening -> thinking -> telling ->
spelling -> ready).
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time

from fastapi import APIRouter, HTTPException

import config as moo_config

from assistant.braille_letters import dots_for

from ..models import SimpleStatus, StoryState
from .. import word_games
from ..services import braille_hw, service_control

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/story")

_OWNER = "story"

_SPELL_FIELDS = word_games.GAME_FIELDS
_game_session: word_games.Session | None = None

_lock = threading.Lock()
_state: dict = {
    "active": False,
    "dry_run": False,
    "phase": "idle",
    "message": None,
    "story": None,
    "parts": 0,
    "spelling_word": None,
    "error": None,
    **_SPELL_FIELDS,
}
_stop = threading.Event()
_was_running = False
_client = None
_audio = None
_age_range = "3-5"
_worker: threading.Thread | None = None

# The co-create question Moo asked at the end of the last part, and the
# child's one-word answer. They steer the NEXT part so the child's idea
# actually shapes the story.
_steer_question = ""
_steer_answer = ""

# Words already spelled this session — so spelling words never repeat.
_spelled_words: set[str] = set()

# Phases during which Moo is actively working — /continue and /stop wait.
_BUSY_PHASES = {"greeting", "listening", "thinking", "telling", "quiz", "spelling"}


def _set(**kw) -> None:
    with _lock:
        _state.update(kw)


def _snapshot() -> StoryState:
    with _lock:
        return StoryState(**_state)


# Answers that carry no idea. Anything else the child says is used as it is.
_FILLER = {
    "yes", "yeah", "yep", "yup", "no", "nope", "ok", "okay", "sure", "please", "yes please",
    "uh huh", "mhm", "alright", "um", "uh", "hmm", "er", "ah", "oh", "i don't know",
    "i dont know", "dunno", "nothing", "you choose", "anything",
}


def _no_idea(text: str) -> bool:
    words = " ".join(re.findall(r"[a-z']+", text.lower()))
    return not words or words in _FILLER


def _listen(question: str) -> str:
    """Record the child's idea for the story.

    The child's words are used as they are. Gemma 3 1B, asked whether an
    answer fits, turned down "I wanna hear the story about medals" and let
    "Yes." through (22 November 2026), so Moo no longer asks a model. An
    answer with no idea in it (silence, "um", "yes") gets one simpler question;
    if that brings no idea either, Moo picks the theme. Safety is kept by the
    check of every story part before it is read.
    """
    answer = (_audio.transcribe(_audio.record_until_silence()) or "").strip()
    logger.info("story idea: %r", answer)
    if _stop.is_set() or not _no_idea(answer):
        return answer

    again = "Tell me what the story should be about. A dragon, a castle, or anything you like!"
    _set(phase="quiz", message=again)
    _audio.speak(again)
    if _stop.is_set():
        return ""
    _set(phase="listening", message=again)
    second = (_audio.transcribe(_audio.record_until_silence()) or "").strip()
    logger.info("story idea, asked again: %r", second)
    return "" if _no_idea(second) else second


def _tell(part_text: str) -> str:
    """Read one story part aloud, all the way through. Returns the text read.

    The model is asked for parts without blanks. If one still slips in, the
    blank is read as "something" so the story never stops mid-sentence.
    """
    from assistant.story import split_blank

    before, after = split_blank(part_text)
    text = part_text if after is None else f"{before}something{after}".strip()
    # Sound cues such as <giggle> are for the voice only: not on the phone,
    # not in the story kept for the next part, never a spelling word.
    shown = " ".join(_CUES.sub("", text).split())
    _set(phase="telling", message=shown)
    _audio.speak(text)
    return shown


_CUES = re.compile(r"<(giggle|laugh|chuckle|sigh|gasp|groan|yawn|cough|sniffle)>", re.I)


# --- Word games (setup_service/word_games.py) ---------------------------------

def _session() -> word_games.Session:
    global _game_session
    if _game_session is None:
        _game_session = word_games.Session(
            client=_client, audio=_audio, age_range=_age_range, set=_set,
            stop=_stop, owner=_OWNER, used_words=_spelled_words,
        )
    return _game_session


def _word_game(part_text: str) -> None:
    ctx = _session()
    word_games.word_game(ctx, part_text)


def _hear() -> str:
    return (_audio.transcribe(_audio.record_until_silence()) or "").strip()


# --- The round: tell, play, ask -------------------------------------------------

_END_WORDS = {"stop", "finish", "finished", "done", "enough", "bye", "goodbye"}

_NEXT_QUESTIONS = (
    "What should happen next? Or say: the end.",
    "What happens next? You can also say: the end.",
)


def _wants_end(heard: str) -> bool:
    words = re.findall(r"[a-z]+", heard.lower())
    if not words:
        return False
    text = " ".join(words)
    if text.startswith("the end") or text in ("end", "no more", "no"):
        return True
    return len(words) <= 4 and bool(set(words) & _END_WORDS)


def _ask_next() -> str | None:
    """Ask what happens next. Returns the child's idea, or None to end."""
    global _steer_question, _steer_answer
    # A fixed, short question every round. A generated one ran on into more
    # story ("She was bursting with ideas! What fun surprise...") and changed
    # every time; the same words each round are easier for a child to learn.
    question = random.choice(_NEXT_QUESTIONS)
    heard = ""
    for attempt in range(2):
        if _stop.is_set():
            return None
        _set(phase="quiz", message=question)
        _audio.speak(question)
        _set(phase="listening", message=question)
        heard = _hear()
        logger.info("next idea: %r", heard)
        if _wants_end(heard):
            return None
        if not _no_idea(heard):
            # Used as the child said it; the part written from it is checked
            # for safety before Moo reads it.
            _steer_question, _steer_answer = question, heard
            return heard
        question = "I didn't hear an idea. What should happen next? Or say the end."
    if heard:
        # Two answers with no idea in them: the story goes on by itself.
        _steer_question, _steer_answer = "", ""
        return ""
    return None


def _story_loop(part: str) -> None:
    """Tell a part, play a word game with it, ask what happens next, repeat."""
    from assistant.story import extend_story, generate_line

    while not _stop.is_set():
        told = _tell(part)
        with _lock:
            prior = _state["story"] or ""
            parts = _state["parts"] + 1
        full = (prior + "\n\n" + told).strip()
        _set(story=full, parts=parts)
        if _stop.is_set():
            return
        _word_game(told)
        if _stop.is_set():
            return
        idea = _ask_next()
        if idea is None:
            _audio.speak(generate_line(_client, "goodbye", _age_range))
            _set(phase="done", message="The end — what a great story we made!")
            return
        _set(phase="thinking", message="Writing the next part…")
        part = extend_story(_client, full, _steer_question, _steer_answer, _age_range)


def _run_first() -> None:
    """Voice-driven session: greeting, theme, then the story loop."""
    from assistant.story import generate_intro, generate_story

    try:
        _set(phase="greeting", message="Moo is saying hello…", error=None)
        intro = generate_intro(_client, _age_range)
        _set(message=intro)
        _audio.speak(intro)
        if _stop.is_set():
            return

        _set(phase="listening", message="Listening for a story idea…")
        theme = _listen("What should our story be about?")
        theme = (theme or "").strip() or "a friendly cow"
        logger.info("story theme: %s", theme)
        if _stop.is_set():
            return

        _set(phase="thinking", message=f"Writing a story about {theme}…")
        story = generate_story(_client, theme, _age_range)
        if _stop.is_set():
            return
        _story_loop(story)
    except Exception as exc:  # noqa: BLE001
        logger.exception("story session failed")
        _set(phase="error", error=str(exc))


def _run_continue() -> None:
    """Continue after an error or a manual pause, from the phone."""
    from assistant.story import extend_story

    try:
        with _lock:
            prior = _state["story"] or ""
        _set(phase="thinking", message="Writing the next part…", error=None)
        part = extend_story(_client, prior, _steer_question, _steer_answer, _age_range)
        if _stop.is_set():
            return
        _story_loop(part)
    except Exception as exc:  # noqa: BLE001
        logger.exception("story extension failed")
        _set(phase="error", error=str(exc))


# One start at a time. The phone app once sent two starts in the same
# second, both got past the check of "active" while the first was still being
# set up, and two sessions ran at once: the intro played twice and one
# session's microphone recorded the other's voice (21 November 2026).
_story_start_lock = threading.Lock()


@router.post("/start", response_model=StoryState)
def story_start() -> StoryState:
    if not _story_start_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Moo is already starting one")
    try:
        return _story_start_unguarded()
    finally:
        _story_start_lock.release()


def _story_start_unguarded() -> StoryState:
    global _was_running, _client, _audio, _age_range, _worker
    global _steer_question, _steer_answer
    with _lock:
        if _state["active"]:
            raise HTTPException(status_code=409, detail="A story session is already running")
    _steer_question = ""
    _steer_answer = ""
    _spelled_words.clear()
    global _game_session
    _game_session = None

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

    _client = make_client(cfg)
    prefs = cfg.get("preferences", {}) or {}
    audio_cfg = cfg.get("audio", {}) or {}
    _age_range = prefs.get("age_range") or "3-5"

    from assistant.audio import AudioIO

    _audio = AudioIO(
        _client,
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
            phase="idle",
            message="Starting…",
            story=None,
            parts=0,
            spelling_word=None,
            error=None,
            **_SPELL_FIELDS,
        )
    _worker = threading.Thread(target=_run_first, daemon=True)
    _worker.start()
    return _snapshot()


@router.post("/continue", response_model=StoryState)
def story_continue() -> StoryState:
    global _worker
    with _lock:
        if not _state["active"]:
            raise HTTPException(status_code=409, detail="No story session running")
        if _state["phase"] in _BUSY_PHASES:
            raise HTTPException(
                status_code=409, detail=f"Moo is busy ({_state['phase']}) — wait a moment"
            )
    _worker = threading.Thread(target=_run_continue, daemon=True)
    _worker.start()
    return _snapshot()


@router.get("/state", response_model=StoryState)
def story_state() -> StoryState:
    return _snapshot()


@router.post("/stop", response_model=SimpleStatus)
def story_stop() -> SimpleStatus:
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
