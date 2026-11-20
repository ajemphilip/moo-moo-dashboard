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
from ..services import braille_hw, service_control

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/story")

_OWNER = "story"
# A letter stays raised until the child lowers every one of its dots. This
# generous cap is only a failsafe so a broken button can't stall forever.
SPELL_LETTER_TIMEOUT = 120.0

_SPELL_FIELDS = dict(
    game_mode=None,
    build_raised=[],
    build_target=[],
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


def _tell(part_text: str) -> str:
    """Read one story part aloud, all the way through. Returns the text read.

    The model is asked for parts without blanks. If one still slips in, the
    blank is read as "something" so the story never stops mid-sentence.
    """
    from assistant.story import split_blank

    before, after = split_blank(part_text)
    text = part_text if after is None else f"{before}something{after}".strip()
    _set(phase="telling", message=text)
    _audio.speak(text)
    return text


# --- Word games ----------------------------------------------------------------
# After each part Moo plays one game with a word from that part. The mix keeps
# it from turning into pressing only: spelling the word, reading one letter,
# or a riddle after which the child builds a letter by raising its dots.

_GAME_WEIGHTS = {
    "0-3": {"spell": 0.6, "name": 0.4, "build": 0.0},
    "3-5": {"spell": 0.4, "name": 0.35, "build": 0.25},
    "5-8": {"spell": 0.34, "name": 0.33, "build": 0.33},
}
_last_game: str | None = None
BUILD_TIMEOUT = 90.0      # seconds before Moo shows the letter
BUILD_HINT_AFTER = 45.0   # seconds before Moo lets the child feel the letter


def _pick_game() -> str:
    global _last_game
    weights = dict(_GAME_WEIGHTS.get(_age_range, _GAME_WEIGHTS["3-5"]))
    # Avoid the same game twice in a row when another one is possible.
    if _last_game and sum(v for k, v in weights.items() if k != _last_game) > 0:
        weights[_last_game] = 0.0
    games = [g for g, w in weights.items() if w > 0]
    game = random.choices(games, weights=[weights[g] for g in games])[0]
    _last_game = game
    return game


def _pick_word(text: str) -> str:
    from assistant.story import pick_spelling_word, pick_story_word

    word = pick_story_word(_client, text, _age_range, exclude=_spelled_words)
    if not [c for c in word if c.isalpha()]:
        word = pick_spelling_word(text, _spelled_words)
    _spelled_words.add(word.lower())
    return word


def _word_game(part_text: str) -> None:
    word = _pick_word(part_text)
    game = _pick_game()
    logger.info("word game: %s with %r", game, word)
    if game == "name":
        _name_letter(word)
    elif game == "build":
        _build_letter(word)
    else:
        _spell_word(word)
    _set(**_SPELL_FIELDS)


def _spell_word(word: str) -> None:
    """Spell `word` one letter at a time.

    Each letter is raised and stays up. The child feels it and presses a
    button for each raised dot to lower it; only when EVERY dot of the letter
    is down does spelling move on. A press on a dot that is not part of the
    letter gets one gentle hint per letter. A generous cap moves on anyway if
    a dot can never be cleared.
    """
    from assistant.story import generate_line

    letters = [c for c in word.upper() if c.isalpha()]
    _set(
        phase="spelling",
        game_mode="spell",
        message=f"Let's spell: {word}",
        spelling_word=word,
        spell_total=len(letters),
    )
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
        driver.render_letter(ch)

        cleared: set[int] = set()
        hinted = False
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
                    if not hinted:
                        hinted = True
                        _audio.speak("That dot is already down. Feel for a raised one.")
            time.sleep(0.08)
        _set(spell_awaiting=False)
        if cleared == dots and not _stop.is_set():
            _audio.play_cue("done")

    try:
        driver.reset_cell()
    except Exception:
        pass
    if not _stop.is_set():
        _audio.speak(f"You spelled {word}!")


def _hear() -> str:
    return (_audio.transcribe(_audio.record_until_silence()) or "").strip()


def _name_letter(word: str) -> None:
    """Raise one letter of `word` and ask the child to say which letter it is."""
    from assistant.story import check_letter, generate_line

    letters = [c for c in word.upper() if c.isalpha()]
    ch = letters[0] if _age_range == "0-3" else random.choice(letters)
    _set(
        phase="spelling",
        game_mode="name",
        spelling_word=word,
        spell_letter=ch,
        spell_total=len(letters),
        spell_index=letters.index(ch),
        message="Which letter is this? Feel the dots and say it out loud.",
    )
    _audio.speak(generate_line(_client, "letter_intro", _age_range, word=word))
    driver = braille_hw.acquire(_OWNER)
    driver.render_letter(ch)
    time.sleep(1.6)  # let the dots rise before asking
    question = "Feel the dots. Which letter is it?"
    for attempt in range(2):
        if _stop.is_set():
            break
        _audio.speak(question)
        _set(phase="listening", message=question)
        heard = _hear()
        logger.info("letter game %s: heard %r", ch, heard)
        _set(phase="spelling")
        if check_letter(_client, ch, heard):
            _audio.play_cue("done")
            _audio.speak(f"Yes! That is the letter {ch}, from our word {word}!")
            break
        if attempt == 0:
            question = "Good try! Feel it once more. Which letter is it?"
        else:
            _audio.speak(
                f"This one is the letter {ch}, like in {word}. Feel it now that you know it."
            )
            time.sleep(3.0)
    try:
        driver.reset_cell()
    except Exception:
        pass


def _build_letter(word: str) -> None:
    """A riddle about `word`, then the child raises the dots of its first letter.

    All dots start down. Pressing a dot raises it, pressing it again lowers
    it. When exactly the dots of the letter are up, the letter is made.
    """
    from assistant.story import check_guess, generate_line, make_riddle

    letters = [c for c in word.upper() if c.isalpha()]
    ch = letters[0]
    dots = set(dots_for(ch))
    driver = braille_hw.acquire(_OWNER)
    try:
        driver.reset_cell()
    except Exception:
        pass

    _set(phase="quiz", game_mode="build", spelling_word=word, spell_letter=None,
         message="A riddle!")
    riddle = make_riddle(_client, word, _age_range)
    _audio.speak(generate_line(_client, "riddle_intro", _age_range))
    _set(message=riddle)
    _audio.speak(riddle)
    if _stop.is_set():
        return
    _set(phase="listening", message=riddle)
    heard = _hear()
    logger.info("riddle %r: heard %r", word, heard)
    if check_guess(_client, word, heard):
        _audio.speak(f"Yes, it is {word}!")
    else:
        _audio.speak(f"Good guess! The answer is {word}.")
    if _stop.is_set():
        return

    _set(phase="spelling", spell_letter=ch, build_raised=[], build_target=sorted(dots),
         message=f"Make the letter {ch}: press its dots to raise them")
    _audio.speak(
        f"{word} starts with the letter {ch}. Can you make the letter {ch}? "
        "Press a dot to raise it, and press it again to lower it."
    )
    driver.lower_on_press = False
    raised: set[int] = set()
    last_toggle = [0.0] * 6
    last_motor = 0.0
    hinted_wrong: set[int] = set()
    hint_given = False
    started = time.time()
    made = False
    try:
        driver.pop_presses()
        while not _stop.is_set() and time.time() - started < BUILD_TIMEOUT:
            for b, t in driver.pop_presses():
                if not 0 <= b < 6:
                    continue
                # Motor current can glitch the button lines. Ignore a press
                # right after a motor started, and a repeat of the same dot
                # within 0.8 s.
                if t - last_motor < 0.15 or t - last_toggle[b] < 0.8:
                    continue
                last_toggle[b] = t
                last_motor = time.monotonic()
                if b in raised:
                    driver.lower_motor(b)
                    raised.discard(b)
                else:
                    driver.raise_motor(b)
                    raised.add(b)
                _set(build_raised=sorted(raised))
                if b in raised and b not in dots and b not in hinted_wrong:
                    hinted_wrong.add(b)
                    _audio.speak(f"That dot is not part of {ch}. Press it again to lower it.")
            if raised == dots:
                made = True
                break
            if not hint_given and time.time() - started > BUILD_HINT_AFTER:
                hint_given = True
                _audio.speak(f"Here is a hint. Feel the letter {ch}.")
                driver.render_letter(ch)
                time.sleep(4.0)
                driver.reset_cell()
                raised = set()
                _set(build_raised=[])
                driver.pop_presses()
                _audio.speak("Now you try. Press the dots to make it.")
            time.sleep(0.08)
        if _stop.is_set():
            return
        if made:
            _audio.play_cue("done")
            _audio.speak(f"You made the letter {ch}! Wonderful!")
            time.sleep(1.5)
        else:
            _audio.speak(f"This is the letter {ch}. Feel it.")
            driver.render_letter(ch)
            time.sleep(3.0)
    finally:
        driver.lower_on_press = True
        try:
            driver.reset_cell()
        except Exception:
            pass


# --- The round: tell, play, ask -------------------------------------------------

_END_WORDS = {"stop", "finish", "finished", "done", "enough", "bye", "goodbye"}


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
    from assistant.story import check_answer, generate_line

    question = generate_line(_client, "next_prompt", _age_range)
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
        if not heard:
            question = "I didn't hear you. What should happen next? Or say the end."
            continue
        fits, reply = check_answer(_client, question, heard, _age_range)
        logger.info("answer check: %r fits=%s", heard, fits)
        if fits:
            if reply:
                _set(message=reply)
                _audio.speak(reply)
            _steer_question, _steer_answer = question, heard
            return heard
        question = reply or question
    if heard:
        _steer_question, _steer_answer = question, heard
        return heard
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
