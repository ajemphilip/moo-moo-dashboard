"""Word games on the braille cell, shared by the story and the games page.

Three games, each played with one word:
  spell  — the word's letters are raised one at a time and the child presses
           each raised dot down
  name   — one letter of the word is raised and the child says which it is
  build  — a riddle about the word, then the child raises the dots of its
           first letter by pressing them

Every function takes a session object `ctx` with: client (OpenAI), audio
(AudioIO), age_range, set (updates the state the phone polls), stop
(threading.Event), owner (braille_hw owner name), used_words (set) and
last_game.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from assistant.braille_letters import dots_for

from .services import braille_hw

logger = logging.getLogger(__name__)

# A letter stays raised until the child lowers every one of its dots. This
# generous cap is only a failsafe so a broken button can't stall forever.
SPELL_LETTER_TIMEOUT = 120.0

# State fields of a game, reset between games.
GAME_FIELDS = dict(
    game_mode=None,
    build_raised=[],
    build_target=[],
    spell_letter=None,
    spell_index=None,
    spell_total=None,
    spell_dots_left=None,
    spell_awaiting=False,
)


@dataclass
class Session:
    client: Any
    audio: Any
    age_range: str
    set: Callable[..., None]
    stop: Any
    owner: str
    used_words: set = field(default_factory=set)
    last_game: str | None = None


# --- Choosing ----------------------------------------------------------------
# After each part Moo plays one game with a word from that part. The mix keeps
# it from turning into pressing only: spelling the word, reading one letter,
# or a riddle after which the child builds a letter by raising its dots.

_GAME_WEIGHTS = {
    "0-3": {"spell": 0.6, "name": 0.4, "build": 0.0},
    "3-5": {"spell": 0.4, "name": 0.35, "build": 0.25},
    "5-8": {"spell": 0.34, "name": 0.33, "build": 0.33},
}
BUILD_TIMEOUT = 90.0      # seconds before Moo shows the letter
BUILD_HINT_AFTER = 45.0   # seconds before Moo lets the child feel the letter


def pick_game(ctx) -> str:
    weights = dict(_GAME_WEIGHTS.get(ctx.age_range, _GAME_WEIGHTS["3-5"]))
    # Avoid the same game twice in a row when another one is possible.
    if ctx.last_game and sum(v for k, v in weights.items() if k != ctx.last_game) > 0:
        weights[ctx.last_game] = 0.0
    games = [g for g, w in weights.items() if w > 0]
    game = random.choices(games, weights=[weights[g] for g in games])[0]
    ctx.last_game = game
    return game


def pick_word(ctx, text: str) -> str:
    from assistant.story import pick_spelling_word, pick_story_word

    word = pick_story_word(ctx.client, text, ctx.age_range, exclude=ctx.used_words)
    if not [c for c in word if c.isalpha()]:
        word = pick_spelling_word(text, ctx.used_words)
    ctx.used_words.add(word.lower())
    return word


def practice_word(ctx) -> str:
    """A word for the games page, where there is no story to take it from."""
    from assistant.story import pick_practice_word

    word = pick_practice_word(ctx.client, ctx.age_range, exclude=ctx.used_words)
    ctx.used_words.add(word.lower())
    return word


def word_game(ctx, part_text: str) -> None:
    """One game with a word from a story part (used by the story)."""
    play(ctx, pick_word(ctx, part_text), pick_game(ctx))


def play(ctx, word: str, game: str) -> None:
    """Play `game` (spell, name or build) with `word`, then clear the game state."""
    logger.info("word game: %s with %r", game, word)
    if game == "name":
        name_letter(ctx, word)
    elif game == "build":
        build_letter(ctx, word)
    else:
        spell_word(ctx, word)
    ctx.set(**GAME_FIELDS)


def spell_word(ctx, word: str) -> None:
    """Spell `word` one letter at a time.

    Each letter is raised and stays up. The child feels it and presses a
    button for each raised dot to lower it; only when EVERY dot of the letter
    is down does spelling move on. A press on a dot that is not part of the
    letter gets one gentle hint per letter. A generous cap moves on anyway if
    a dot can never be cleared.
    """
    from assistant.story import generate_line

    letters = [c for c in word.upper() if c.isalpha()]
    ctx.set(
        phase="spelling",
        game_mode="spell",
        message=f"Let's spell: {word}",
        spelling_word=word,
        spell_total=len(letters),
    )
    opener = generate_line(ctx.client, "spell_intro", ctx.age_range, word=word)
    ctx.audio.speak(
        f"{opener} Feel each letter, then press a button for every raised "
        "dot to clear it."
    )
    driver = braille_hw.acquire(ctx.owner)

    for i, ch in enumerate(letters):
        if ctx.stop.is_set():
            break
        dots = set(dots_for(ch))
        ctx.set(
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
        ctx.audio.speak(f"The letter {ch}.")
        driver.render_letter(ch)

        cleared: set[int] = set()
        hinted = False
        ctx.set(spell_awaiting=True)
        deadline = time.time() + SPELL_LETTER_TIMEOUT
        while cleared != dots and not ctx.stop.is_set() and time.time() < deadline:
            try:
                presses = driver.pop_presses()
            except Exception:
                presses = []
            for pressed, _t in presses:
                if pressed in dots and pressed not in cleared:
                    cleared.add(pressed)
                    ctx.set(spell_dots_left=len(dots - cleared))
                elif pressed not in dots:
                    logger.info("spelling %s: press on dot %d, not in the letter", ch, pressed)
                    if not hinted:
                        hinted = True
                        ctx.audio.speak("That dot is already down. Feel for a raised one.")
            time.sleep(0.08)
        ctx.set(spell_awaiting=False)
        if cleared == dots and not ctx.stop.is_set():
            ctx.audio.play_cue("done")

    try:
        driver.reset_cell()
    except Exception:
        pass
    if not ctx.stop.is_set():
        ctx.audio.speak(f"You spelled {word}!")


def hear(ctx) -> str:
    return (ctx.audio.transcribe(ctx.audio.record_until_silence()) or "").strip()


def name_letter(ctx, word: str) -> None:
    """Raise one letter of `word` and ask the child to say which letter it is."""
    from assistant.story import check_letter, generate_line

    letters = [c for c in word.upper() if c.isalpha()]
    ch = letters[0] if ctx.age_range == "0-3" else random.choice(letters)
    ctx.set(
        phase="spelling",
        game_mode="name",
        spelling_word=word,
        spell_letter=ch,
        spell_total=len(letters),
        spell_index=letters.index(ch),
        message="Which letter is this? Feel the dots and say it out loud.",
    )
    # Fixed words: a generated intro named the letter before the child felt it.
    ctx.audio.speak("Here is a letter. Feel it, and tell me which letter it is.")
    driver = braille_hw.acquire(ctx.owner)
    # While the child explores the letter and guesses it, pressing the dots
    # does nothing, so feeling the letter cannot lower it by accident.
    driver.lower_on_press = False
    try:
        driver.render_letter(ch)
        time.sleep(1.6)  # let the dots rise before asking
        question = "Feel the dots. Which letter is it?"
        for attempt in range(2):
            if ctx.stop.is_set():
                break
            ctx.audio.speak(question)
            ctx.set(phase="listening", message=question)
            heard = hear(ctx)
            logger.info("letter game %s: heard %r", ch, heard)
            ctx.set(phase="spelling")
            if check_letter(ctx.client, ch, heard):
                ctx.audio.play_cue("done")
                ctx.audio.speak(f"{random.choice(_CHEERS)} It's {ch}! {_like_in(ch, word)}")
                break
            if attempt == 0:
                question = "Good try! Feel it once more. Which letter is it?"
            else:
                ctx.audio.speak(
                    f"This one is {ch}! {_like_in(ch, word)} Feel it now that you know it."
                )
                time.sleep(3.0)
        if ctx.stop.is_set():
            return

        # Only now do the buttons clear the letter.
        driver.pop_presses()
        driver.lower_on_press = True
        ctx.set(message=f"Press every raised dot of {ch} to clear it",
                spell_dots_left=len(dots_for(ch)), spell_awaiting=True)
        ctx.audio.speak("Now press each raised dot to clear the letter.")
        _clear_letter(ctx, driver, ch)
    finally:
        driver.lower_on_press = True
        try:
            driver.reset_cell()
        except Exception:
            pass


_CHEERS = ("Yes!", "You got it!", "Wow, well done!", "Amazing!", "That's right!")

# One easy word for every letter, so Moo can give a second example.
_EXAMPLES = {
    "A": "apple", "B": "ball", "C": "cat", "D": "dog", "E": "elephant", "F": "fish",
    "G": "goat", "H": "hat", "I": "igloo", "J": "jam", "K": "kite", "L": "lion",
    "M": "moon", "N": "nest", "O": "octopus", "P": "pig", "Q": "queen", "R": "rabbit",
    "S": "sun", "T": "tree", "U": "umbrella", "V": "van", "W": "whale", "X": "fox",
    "Y": "yo-yo", "Z": "zebra",
}


def _like_in(ch: str, word: str) -> str:
    """'Like in egg, or in elephant!' — the game word and one more example."""
    other = _EXAMPLES.get(ch.upper(), "")
    if other and other.lower() != word.lower():
        return f"Like in {word}, or in {other}!"
    return f"Like in {word}!"


def _clear_letter(ctx, driver, ch: str) -> bool:
    """Wait until the child has pressed every raised dot of `ch` down.

    Returns True when the letter was cleared. A press on a dot that is not
    part of the letter gets one gentle hint. A generous cap moves on anyway.
    """
    dots = set(dots_for(ch))
    cleared: set[int] = set()
    hinted = False
    deadline = time.time() + SPELL_LETTER_TIMEOUT
    while cleared != dots and not ctx.stop.is_set() and time.time() < deadline:
        try:
            presses = driver.pop_presses()
        except Exception:
            presses = []
        for pressed, _t in presses:
            if pressed in dots and pressed not in cleared:
                cleared.add(pressed)
                ctx.set(spell_dots_left=len(dots - cleared))
            elif pressed not in dots:
                logger.info("letter %s: press on dot %d, not in the letter", ch, pressed)
                if not hinted:
                    hinted = True
                    ctx.audio.speak("That dot is already down. Feel for a raised one.")
        time.sleep(0.08)
    ctx.set(spell_awaiting=False)
    if cleared == dots and not ctx.stop.is_set():
        ctx.audio.play_cue("done")
        return True
    return False


def build_letter(ctx, word: str) -> None:
    """A riddle about `word`, then the child raises the dots of its first letter.

    All dots start down. Pressing a dot raises it, pressing it again lowers
    it. When exactly the dots of the letter are up, the letter is made.
    """
    from assistant.story import check_guess, generate_line, make_riddle

    letters = [c for c in word.upper() if c.isalpha()]
    ch = letters[0]
    dots = set(dots_for(ch))
    driver = braille_hw.acquire(ctx.owner)
    try:
        driver.reset_cell()
    except Exception:
        pass

    ctx.set(phase="quiz", game_mode="build", spelling_word=word, spell_letter=None,
         message="A riddle!")
    riddle = make_riddle(ctx.client, word, ctx.age_range)
    ctx.audio.speak(generate_line(ctx.client, "riddle_intro", ctx.age_range))
    ctx.set(message=riddle)
    ctx.audio.speak(riddle)
    if ctx.stop.is_set():
        return
    ctx.set(phase="listening", message=riddle)
    heard = hear(ctx)
    logger.info("riddle %r: heard %r", word, heard)
    if check_guess(ctx.client, word, heard):
        ctx.audio.speak(f"Yes, it is {word}!")
    else:
        ctx.audio.speak(f"Good guess! The answer is {word}.")
    if ctx.stop.is_set():
        return

    ctx.set(phase="spelling", spell_letter=ch, build_raised=[], build_target=sorted(dots),
         message=f"Make the letter {ch}: press its dots to raise them")
    ctx.audio.speak(
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
        while not ctx.stop.is_set() and time.time() - started < BUILD_TIMEOUT:
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
                ctx.set(build_raised=sorted(raised))
                if b in raised and b not in dots and b not in hinted_wrong:
                    hinted_wrong.add(b)
                    ctx.audio.speak(f"That dot is not part of {ch}. Press it again to lower it.")
            if raised == dots:
                made = True
                break
            if not hint_given and time.time() - started > BUILD_HINT_AFTER:
                hint_given = True
                ctx.audio.speak(f"Here is a hint. Feel the letter {ch}.")
                driver.render_letter(ch)
                time.sleep(4.0)
                driver.reset_cell()
                raised = set()
                ctx.set(build_raised=[])
                driver.pop_presses()
                ctx.audio.speak("Now you try. Press the dots to make it.")
            time.sleep(0.08)
        if ctx.stop.is_set():
            return
        if made:
            ctx.audio.play_cue("done")
            ctx.audio.speak(f"You made the letter {ch}! Wonderful!")
            time.sleep(1.5)
        else:
            ctx.audio.speak(f"This is the letter {ch}. Feel it.")
            driver.render_letter(ch)
            time.sleep(3.0)
    finally:
        driver.lower_on_press = True
        try:
            driver.reset_cell()
        except Exception:
            pass


