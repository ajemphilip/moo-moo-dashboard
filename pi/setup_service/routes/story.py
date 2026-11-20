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
import re
import threading
import time

from fastapi import APIRouter, HTTPException

import config as moo_config

from assistant.braille_letters import dots_for

from ..models import SimpleStatus, StoryState
from ..services import braille_hw, service_control

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/story")

_OWNER = "story"
# A letter stays raised until the child lowers every one of its dots. This
# generous cap is only a failsafe so a broken button can't stall forever.
SPELL_LETTER_TIMEOUT = 120.0

_SPELL_FIELDS = dict(
    spell_letter=None,
    spell_index=None,
    spell_total=None,
    spell_dots_left=None,
    spell_awaiting=False,
)

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


def _listen(question: str) -> str:
    """Record the child's answer and make sure Moo can use it.

    The answer is checked against the question. If it fits, Moo repeats it
    back in one short sentence so the child knows they were heard. If it
    does not fit (silence, filler, cut off, off topic or unsafe), Moo asks
    once more in simpler words and listens again. The second answer is used
    as it is, so the story never gets stuck.
    """
    from assistant.story import check_answer

    answer = (_audio.transcribe(_audio.record_until_silence()) or "").strip()
    if _stop.is_set():
        return answer
    fits, reply = check_answer(_client, question, answer, _age_range)
    logger.info("answer check: %r fits=%s", answer, fits)
    if fits:
        if reply:
            _set(message=reply)
            _audio.speak(reply)
        return answer

    again = reply or question
    _set(phase="quiz", message=again)
    _audio.speak(again)
    if _stop.is_set():
        return answer
    _set(phase="listening", message=again)
    second = (_audio.transcribe(_audio.record_until_silence()) or "").strip()
    logger.info("answer retry: %r", second)
    if not second:
        return answer
    fits2, reply2 = check_answer(_client, again, second, _age_range)
    if fits2 and reply2:
        _set(message=reply2)
        _audio.speak(reply2)
    return second


def _tell_and_fill(part_text: str) -> tuple[str, str]:
    """Read a story part aloud, let the child fill in its blank.

    Each part is written with one ``___`` blank. Moo reads up to the blank,
    the child says the missing word, then Moo reads the rest with the word
    in place. Returns the completed part text and the child's word (for
    spelling). If the model left no blank, Moo reads the whole part and asks
    a co-create question instead, so the flow never breaks.
    """
    global _steer_question, _steer_answer
    from assistant.story import (
        ask_blank_question,
        ask_cocreate_question,
        generate_line,
        split_blank,
        word_to_spell,
    )

    before, after = split_blank(part_text)

    if after is not None:
        # Fill-in-the-blank flow — the child's word lands inside the story.
        _steer_question, _steer_answer = "", ""
        _set(phase="telling", message="Here comes the fun part — listen!")
        _audio.speak(generate_line(_client, "blank_intro", _age_range))
        _audio.speak(before)
        if _stop.is_set():
            return part_text.replace("___", "____"), ""

        # An enthusiastic, clear question so the blank never feels abrupt.
        question = ask_blank_question(_client, before, after, _age_range)
        _set(phase="quiz", message=question)
        _audio.speak(question)
        if _stop.is_set():
            return part_text.replace("___", "____"), ""

        _set(phase="listening", message=question)
        answer = _listen(question)
        word = word_to_spell(answer, part_text)
        logger.info("blank fill: heard %r -> %s", answer, word)

        completed = f"{before}{word}{after}".strip()
        cheer = generate_line(_client, "cheer", _age_range, word=word)
        _set(phase="telling", message=f"You said {word}! {completed}")
        # Don't re-read the part the child just heard. The cheer line
        # announces their word; then we continue from after the blank.
        after_tail = after.strip().lstrip(".").strip()
        _audio.speak(f"{cheer} {after_tail}")
        return completed, word

    # Fallback: model left no blank — read the part, then ask for an idea.
    _set(phase="telling", message=part_text)
    _audio.speak(part_text)
    if _stop.is_set():
        return part_text, ""
    _set(phase="quiz", message="Moo wants your child's idea…")
    question = ask_cocreate_question(_client, part_text, _age_range)
    _set(message=question)
    _audio.speak(question)
    if _stop.is_set():
        return part_text, ""
    _set(phase="listening", message="Listening for your idea…")
    answer = _listen(question)
    _steer_question, _steer_answer = question, answer
    return part_text, word_to_spell(answer, part_text)


def _spell_word(story_full: str) -> None:
    """Spell a meaningful word from the story, one letter at a time.

    Each letter is rendered with the SAME driver call as the letter-practice
    exercise (`render_letter`): its dots up, every other dot down, and they
    stay up — no timer. The child feels the letter and presses a button for
    each raised dot to lower it; only when EVERY dot of the letter is down
    does spelling move to the next letter. A generous safety cap advances
    anyway if a dot can never be cleared.
    """
    from assistant.story import generate_line, pick_spelling_word, pick_story_word

    # The LLM picks a meaningful word, avoiding any already spelled this session.
    word = pick_story_word(_client, story_full, _age_range, exclude=_spelled_words)
    letters = [c for c in word.upper() if c.isalpha()]
    if not letters:
        word = pick_spelling_word(story_full, _spelled_words)
        letters = [c for c in word.upper() if c.isalpha()]
    _spelled_words.add(word.lower())

    _set(
        phase="spelling",
        message=f"Let's spell: {word}",
        spelling_word=word,
        spell_total=len(letters),
    )
    # A cheerful sentence to celebrate the story before the spelling game.
    _audio.speak(generate_line(_client, "praise", _age_range))
    opener = generate_line(_client, "spell_intro", _age_range, word=word)
    _audio.speak(
        f"{opener} Feel each letter, then press a button for every raised "
        "dot to clear it."
    )
    driver = braille_hw.acquire(_OWNER)

    for i, ch in enumerate(letters):
        if _stop.is_set():
            break
        dots = set(dots_for(ch))
        _set(
            spell_letter=ch,
            spell_index=i,
            spell_dots_left=len(dots),
            spell_awaiting=False,
            message=f"Letter {ch} — press every raised dot to clear it",
        )
        # Drop presses left over from the previous letter BEFORE this one is
        # announced, so a press made while Moo speaks or the dots rise counts.
        try:
            driver.pop_presses()
        except Exception:
            pass
        _audio.speak(f"The letter {ch}.")

        # Render this letter exactly like the letter-practice exercise:
        # its dots up, every other dot down, left raised until cleared.
        driver.render_letter(ch)

        # Wait until the child has lowered EVERY dot of this letter.
        cleared: set[int] = set()
        _set(spell_awaiting=True)
        deadline = time.time() + SPELL_LETTER_TIMEOUT
        while cleared != dots and not _stop.is_set() and time.time() < deadline:
            try:
                presses = driver.pop_presses()
            except Exception:
                presses = []
            for pressed, _t in presses:
                if pressed in dots and pressed not in cleared:
                    cleared.add(pressed)
                    _set(spell_dots_left=len(dots - cleared))
                elif pressed not in dots:
                    logger.info("spelling %s: press on dot %d, not in the letter", ch, pressed)
            time.sleep(0.08)
        _set(spell_awaiting=False)
        # The next letter's render lowers any dots still up.

    try:
        driver.reset_cell()  # leave the cell flat after the last letter
    except Exception:
        pass
    _set(**_SPELL_FIELDS)


_NO_WORDS = {"no", "nope", "nah", "stop", "done", "finish", "end", "enough", "nothing"}
_YES_WORDS = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "please",
    "more", "another", "again", "ready", "continue",
}


def _ask_continue() -> bool:
    """Verbally ask the child if they want more; parse yes/no from the answer.

    Returns True to continue, False to end. Silence counts as no (child
    likely wandered off). Any clear "no" word ends; otherwise any spoken
    response counts as yes — kids' speech is hard to transcribe perfectly.
    """
    from assistant.story import generate_line

    _set(phase="quiz", message="Should we hear more? Say yes or no.")
    question = generate_line(_client, "continue_prompt", _age_range)
    _set(message=question)
    _audio.speak(question)
    if _stop.is_set():
        return False

    _set(phase="listening", message="Say yes to keep going, or no to finish.")
    answer = (_audio.transcribe(_audio.record_until_silence()) or "").strip().lower()
    logger.info("continue answer: %r", answer)
    if not answer:
        return False
    tokens = set(re.findall(r"[a-z]+", answer))
    if tokens & _NO_WORDS:
        return False
    if tokens & _YES_WORDS:
        return True
    return True  # spoke something but unclear — default to continue


def _run_first() -> None:
    """Voice-driven session: part 1, then loop on voice yes/no for more parts.

    Replaces the old "tap Continue" model — after each part Moo asks if the
    child wants to keep going, listens for their answer, and either extends
    the story or ends with a goodbye.
    """
    from assistant.story import (
        extend_story,
        generate_intro,
        generate_line,
        generate_story,
    )

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

        completed, _word = _tell_and_fill(story)
        _set(story=completed, parts=1)
        if _stop.is_set():
            return
        _spell_word(completed)

        # Voice-driven loop: ask "more?" and either extend or end.
        while not _stop.is_set():
            if not _ask_continue():
                _audio.speak(generate_line(_client, "goodbye", _age_range))
                _set(phase="done", message="The end — what a great story we made!")
                return

            with _lock:
                prior = _state["story"] or ""
            _set(phase="thinking", message="Writing the next part…")
            extension = extend_story(
                _client, prior, _steer_question, _steer_answer, _age_range
            )
            if _stop.is_set():
                return

            completed_ext, _word = _tell_and_fill(extension)
            full = (prior + "\n\n" + completed_ext).strip()
            with _lock:
                parts = _state["parts"] + 1
            _set(story=full, parts=parts)
            if _stop.is_set():
                return
            _spell_word(full)
    except Exception as exc:  # noqa: BLE001
        logger.exception("story session failed")
        _set(phase="error", error=str(exc))


def _run_continue() -> None:
    from assistant.story import extend_story, generate_line

    try:
        with _lock:
            prior = _state["story"] or ""

        _set(phase="thinking", message="Writing the next part…", error=None)
        extension = extend_story(
            _client, prior, _steer_question, _steer_answer, _age_range
        )
        if _stop.is_set():
            return

        completed_ext, _word = _tell_and_fill(extension)
        full = (prior + "\n\n" + completed_ext).strip()
        with _lock:
            parts = _state["parts"] + 1
        _set(story=full, parts=parts)
        if _stop.is_set():
            return

        _spell_word(full)
        _audio.speak(generate_line(_client, "continue_prompt", _age_range))
        _set(phase="ready", message="Tap Continue for more, or Stop to finish.")
    except Exception as exc:  # noqa: BLE001
        logger.exception("story extension failed")
        _set(phase="error", error=str(exc))


@router.post("/start", response_model=StoryState)
def story_start() -> StoryState:
    global _was_running, _client, _audio, _age_range, _worker
    global _steer_question, _steer_answer
    with _lock:
        if _state["active"]:
            raise HTTPException(status_code=409, detail="A story session is already running")
    _steer_question = ""
    _steer_answer = ""
    _spelled_words.clear()

    cfg = moo_config.load_config()
    api_key = cfg.get("openai_api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="No OpenAI API key configured")

    try:
        import openai
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="openai package not installed") from exc

    _was_running = service_control.is_assistant_running()
    if _was_running:
        service_control.stop_assistant()
        time.sleep(1.0)

    _client = openai.OpenAI(api_key=api_key)
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
