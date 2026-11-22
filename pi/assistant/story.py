"""GPT-4 storytelling with a moderation gate.

Every generated story passes through OpenAI's moderation endpoint before TTS.
If flagged, regenerate up to twice with a stricter prompt; on third failure
fall back to a static safe story so the experience never blocks on safety.
"""

from __future__ import annotations

import json
import logging
import random
import re

logger = logging.getLogger(__name__)

CHAT_MODEL = "gpt-4o-mini"
MODERATION_MODEL = "omni-moderation-latest"

SAFE_FALLBACK_STORY = (
    "Once upon a time, a small calf named Moo discovered a meadow full of "
    "soft yellow buttercups. Moo skipped from flower to flower, learning "
    "their names and giggling with the bees. When the sun set, Moo curled "
    "up under a friendly oak tree and dreamed of new friends to meet "
    "tomorrow."
)

SAFE_FALLBACK_NOUN = "moo"

# Each story part is written with exactly one fill-in blank for the child.
BLANK_TOKEN = "___"
_BLANK_INSTRUCTION = (
    " Leave exactly ONE blank for the child to fill in — a single missing "
    "word such as a creature, an object, a place, or a name. Write the blank "
    "as exactly three underscores (___). Put the blank mid-sentence, not at "
    "the very end, and make sure the sentence still makes sense with any "
    "simple word dropped into it."
)


# Set by the session when the voice is Orpheus, which performs sound cues.
EXPRESSIVE_CUES = False


def _system_prompt(age_range: str | None, strict: bool = False) -> str:
    age = age_range or "3-5"
    base = (
        "You are Moo, a gentle storyteller for young children aged "
        f"{age}. Tell warm, simple stories using short sentences and concrete "
        "imagery. Sound cheerful, warm, and gently excited — playful, never "
        "flat. Never include violence, scary content, or anything "
        "frightening. Keep each part very short — 3 or 4 simple sentences, "
        "roughly 40 words, so it reads aloud in about 15 seconds. Tell each "
        "part all the way through without stopping in the middle, and end it "
        "at a natural moment where the story could go on. End the part with "
        "a statement, never with a question to the child — Moo asks what "
        "happens next separately. Reply with only the words Moo reads aloud: "
        "no title, no introduction such as 'Here is the story', no notes."
    )
    if EXPRESSIVE_CUES:
        base += (
            " The voice can perform sound cues. Where it truly fits, add at most "
            "two of these, written exactly so: <giggle> <laugh> <gasp> <sigh> <chuckle>."
        )
    if strict:
        base += (
            " Use only soft, comforting themes — friendship, kindness, "
            "playful animals, simple discoveries. Avoid any conflict."
        )
    return base


def generate_intro(client, age_range: str | None) -> str:
    resp = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": _system_prompt(age_range)},
            {
                "role": "user",
                "content": (
                    "Greet the child warmly, then clearly and explicitly ask "
                    "them to SAY OUT LOUD what kind of story they would like — "
                    "a theme, a character, or an idea. Make it obvious they "
                    "should speak their idea aloud after you finish. Two or "
                    "three short sentences, fresh wording every time."
                ),
            },
        ],
        max_tokens=120,
    )
    return resp.choices[0].message.content.strip()


# Short spoken lines — generated fresh each time so greetings, cheers and
# lead-ins never repeat. (kind -> (instruction, fallback)).
_LINES: dict[str, tuple[str, str]] = {
    "blank_intro": (
        "Say ONE short, excited sentence inviting the child to help finish "
        "the story by filling in a missing word.",
        "Here comes the fun part — let's finish this together!",
    ),
    "cheer": (
        "Say ONE short, joyful cheer celebrating the wonderful word the child "
        "just chose: '{word}'.",
        "Yes — what a wonderful idea!",
    ),
    "spell_intro": (
        "Say ONE short, encouraging sentence inviting the child to feel and "
        "spell their special word '{word}' on the Braille cell.",
        "Now let's feel and spell our special word!",
    ),
    "praise": (
        "Say ONE short, warm cheer praising the child for finishing the story "
        "part so well.",
        "Wonderful work — you are a great storyteller!",
    ),
    "continue_prompt": (
        "Ask the child warmly and excitedly, in ONE short sentence, if they "
        "want to hear MORE of the story. Tell them to answer YES to keep "
        "going or NO if they want to finish.",
        "Would you like to hear more of our story? Say yes or no.",
    ),
    "next_prompt": (
        "Ask the child in ONE short, excited sentence what should happen next "
        "in the story, and tell them they can say 'the end' when they want to "
        "finish.",
        "What should happen next? Or say 'the end' to finish our story.",
    ),
    "letter_intro": (
        "Say ONE short, playful sentence inviting the child to feel a letter "
        "from the word '{word}' on the Braille cell and say out loud which "
        "letter it is.",
        "Here is a letter from our word. Feel it and tell me which letter it is!",
    ),
    "riddle_intro": (
        "Say ONE short, playful sentence telling the child you have a little "
        "riddle for them.",
        "I have a little riddle for you!",
    ),
    "goodbye": (
        "Say ONE short, warm farewell as the storytelling session ends.",
        "What a wonderful story we told together — goodbye for now!",
    ),
}


def generate_line(client, kind: str, age_range: str | None = None, **ctx) -> str:
    """Generate one short, fresh spoken line (greeting, cheer, lead-in).

    `kind` selects the purpose from `_LINES`; `ctx` fills placeholders such
    as {word}. Falls back to a fixed line if the model call fails.
    """
    instruction, fallback = _LINES.get(kind, ("", ""))
    try:
        instruction = instruction.format(**ctx)
    except Exception:
        pass
    try:
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": _system_prompt(age_range)},
                {
                    "role": "user",
                    "content": instruction + " Vary the wording so it feels "
                    "fresh. Reply with just the sentence.",
                },
            ],
            max_tokens=40,
        )
        return resp.choices[0].message.content.strip() or fallback
    except Exception:
        return fallback


_INTRO_HINT = re.compile(r"\b(hello|hi|hey|here is|here's|story|tale|once more|let's begin)\b", re.I)


def clean_part(text: str, max_sentences: int | None = None) -> str:
    """Make a story part read as story only.

    Smaller and local models sometimes add a greeting before the story
    ("Hello! Moo is so happy to tell you a tale!") or a question to the child,
    although the prompt asks for neither. Moo asks its own
    question after every part, so both are removed here.
    """
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if len(paras) > 1 and len(paras[0].split()) <= 20 and _INTRO_HINT.search(paras[0]):
        paras = paras[1:]
    out = " ".join(" ".join(p.split()) for p in paras)
    out = re.sub(r"^\s*(here'?s|here is)[^.!:]*[:.!]\s*", "", out, flags=re.I)
    sentences = re.split(r"(?<=[.!?])\s+", out)
    # A question anywhere in the part goes, not only one at the end: Gemma 3
    # 12B wrote one into six of eight openings in the test of 20 November
    # 2026. A question a character asks inside a sentence ("Who is there?"
    # asked Pip.) stays, because that sentence does not end with it.
    kept = [x for x in sentences if not x.rstrip().rstrip("\"'”’)").endswith("?")]
    if not kept:  # never return nothing
        kept = sentences[:1]
    # Moo ends the story itself, when the child says so. Gemma 3 1B closed a
    # middle part with "The end" and Moo then asked what happens next
    # (22 November 2026).
    out = " ".join(kept).strip()
    out = re.sub(r"[\s.!,]*\bthe\s+end\b[\s.!]*$", "", out, flags=re.I).strip()
    if out and out[-1] not in ".!?\"'”’":
        out += "."
    if max_sentences:
        # Asked for 3 or 4 sentences, the same model wrote 11, which took
        # almost a minute on a small laptop and wandered off the story.
        parts = re.split(r"(?:(?<=[.!?])|(?<=[.!?][\"'”’]))\s+", out)
        out = " ".join(parts[:max_sentences]).strip()
    return out or " ".join(sentences[:1]).strip()


def generate_story(client, theme: str, age_range: str | None) -> str:
    """Generate a story about `theme`, gated through moderation."""
    for attempt in range(3):
        strict = attempt > 0
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": _system_prompt(age_range, strict=strict)},
                {
                    "role": "user",
                    "content": (
                        f"Tell the opening of a gentle story about: {theme}. "
                        "It should feel complete on its own but leave room to "
                        "continue. 3 or 4 short sentences, with no blanks or missing words. No questions to the child."
                    ),
                },
            ],
            max_tokens=160,
        )
        story = clean_part(resp.choices[0].message.content or "")
        if story and _is_safe(client, story):
            return story
        logger.warning("Story flagged by moderation (attempt %d), retrying strictly", attempt + 1)

    logger.warning("All moderation retries exhausted; using fallback story")
    return SAFE_FALLBACK_STORY


def _norm(sentence: str) -> str:
    return " ".join(re.findall(r"[a-z']+", sentence.lower()))


def _drop_repeats(part: str, story_so_far: str) -> str:
    """Remove sentences of a new part that the story already has.

    Gemma 3 1B began its second part with the whole first part, word for word,
    and added two sentences (22 November 2026). Moo would have read it again.
    """
    told = {_norm(x) for x in re.split(r"(?<=[.!?])\s+", story_so_far) if _norm(x)}
    kept = [x for x in re.split(r"(?<=[.!?])\s+", part) if _norm(x) and _norm(x) not in told]
    return " ".join(kept).strip()


def extend_story(
    client,
    story_so_far: str,
    question: str = "",
    answer: str = "",
    age_range: str | None = None,
) -> str:
    """Continue the SAME story coherently.

    `story_so_far` is the full accumulated story (every part read so far), so
    the new scene stays consistent with everything that happened. `question`
    is what Moo just asked the child and `answer` is what the child said —
    together they steer the next scene (a new name, a colour, a feeling, or a
    plot turn), woven in naturally. Either may be empty.
    """
    answer = answer.strip()
    question = question.strip()
    if answer and question:
        want = (
            f' Moo asked the child: "{question}" The child answered: '
            f'"{answer}". Weave the child\'s answer into this part naturally.'
        )
    elif answer:
        want = f" The child would like this next: {answer}. Weave it in naturally."
    else:
        want = ""
    for attempt in range(3):
        strict = attempt > 0
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": _system_prompt(age_range, strict=strict)},
                {
                    "role": "user",
                    "content": (
                        "Here is the story so far:\n\n"
                        f"{story_so_far}\n\n"
                        "Continue the SAME story with one more short, gentle scene "
                        "that follows naturally from what already happened — keep "
                        "the same characters and place." + want +
                        " 3 or 4 short sentences, with no blanks or missing words. No questions to the child."
                    ),
                },
            ],
            max_tokens=160,
        )
        extension = _drop_repeats(
            clean_part(resp.choices[0].message.content or "", max_sentences=None), story_so_far
        )
        extension = clean_part(extension, max_sentences=5) if extension else ""
        if extension and _is_safe(client, extension):
            return extension

    return "And then, Moo took a deep breath and smiled, ready for one more sweet adventure tomorrow."


def split_blank(text: str) -> tuple[str, str | None]:
    """Split a story part at its fill-in blank.

    Returns ``(before, after)`` around the first ``___`` token, with any
    further blanks collapsed out of `after`. Returns ``(text, None)`` when the
    model left no blank, so callers can fall back gracefully.
    """
    idx = text.find(BLANK_TOKEN)
    if idx < 0:
        return text, None
    before = text[:idx]
    after = text[idx + len(BLANK_TOKEN):].replace(BLANK_TOKEN, "")
    return before, after


def ask_blank_question(
    client, before: str, after: str, age_range: str | None = None
) -> str:
    """An enthusiastic question telling the child what word the blank needs.

    Given the text just before and after the blank, Moo asks — warmly and
    excitedly — exactly what kind of word to imagine, and invites the child
    to say it out loud. This makes the blank feel inviting, not abrupt.
    """
    try:
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": _system_prompt(age_range)},
                {
                    "role": "user",
                    "content": (
                        "A children's story has a blank the child will fill "
                        "in. The words BEFORE the blank are: "
                        f'"{before.strip()}". The words AFTER the blank are: '
                        f'"{after.strip()}". Write ONE short, very '
                        "enthusiastic question that makes it clear what kind "
                        "of word belongs in the blank (a creature, an object, "
                        "a name, a place, a colour...) and excitedly invites "
                        "the child to say their word out loud. Warm, playful, "
                        "one sentence."
                    ),
                },
            ],
            max_tokens=50,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return "Ooh, what do you think happens here? Say your word out loud!"


def pick_spelling_word(story: str, exclude: set[str] | None = None) -> str:
    """Pick a short, simple noun from the story for the child to spell.

    `exclude` is a set of already-used words to skip so spellings don't
    repeat across a session.
    """
    exclude = {w.lower() for w in (exclude or set())}
    candidates = [
        w.lower()
        for w in re.findall(r"[A-Za-z]+", story)
        if 3 <= len(w) <= 5
        and w.lower() in _COMMON_NOUNS
        and w.lower() not in exclude
    ]
    if candidates:
        return random.choice(candidates)
    return SAFE_FALLBACK_NOUN


def pick_story_word(
    client,
    story_so_far: str,
    age_range: str | None = None,
    exclude: set[str] | None = None,
) -> str:
    """Use the LLM to choose ONE meaningful, easy word from the story to spell.

    The word must matter to the story — a character, a place, an animal, a
    key object (often the very word the child added) — and be short and
    simple enough for a young child to spell. `exclude` is the set of words
    already spelled this session; the choice avoids them so words never
    repeat. Falls back to a plain noun pulled from the text on failure.
    """
    exclude = {w.lower() for w in (exclude or set())}
    avoid = ""
    if exclude:
        avoid = (
            " Do NOT choose any of these already-used words: "
            + ", ".join(sorted(exclude))
            + " — pick a different one."
        )
    try:
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": _system_prompt(age_range)},
                {
                    "role": "user",
                    "content": (
                        "From the story below, choose ONE word for the child "
                        "to spell. It must be important and meaningful to the "
                        "story — a character, place, animal, or key object — "
                        "and easy for a young child. Prefer a shorter word, "
                        f"about {_word_length(age_range)} letters, but it must be "
                        "a complete real word written exactly as it appears in "
                        "the story. Never shorten, cut or change a word." + avoid
                        + " Reply with ONLY "
                        "that single "
                        f"word.\n\n{story_so_far}"
                    ),
                },
            ],
            max_tokens=10,
        )
        tokens = re.findall(r"[A-Za-z]+", resp.choices[0].message.content or "")
        story_words = {w.lower() for w in re.findall(r"[A-Za-z]+", story_so_far)}
        if tokens and tokens[0].lower() not in exclude:
            word = tokens[0].lower()
            # The word must be a whole word from the story, never a trimmed one
            # such as "boog" for "booger".
            if word in story_words:
                return word
            logger.info("pick_story_word: %r is not a word of the story; using fallback", word)
    except Exception as exc:  # noqa: BLE001
        logger.warning("pick_story_word failed (%s); using fallback", exc)
    return pick_spelling_word(story_so_far, exclude)


def ask_cocreate_question(
    client, story_so_far: str, age_range: str | None = None
) -> str:
    """A warm, playful question inviting the child to ADD to the story.

    Always answerable in a single word — and that word becomes part of the
    story: a name for a new character or place, a magical object, a creature
    they meet, a treasure they find. It's an invitation to imagine together,
    not a quiz. The answer is both spelled on the cell and woven into the
    next part of the story.
    """
    try:
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": _system_prompt(age_range)},
                {
                    "role": "user",
                    "content": (
                        "The story so far is below. Ask the child ONE warm, "
                        "playful question that invites them to ADD something "
                        "to the story — something we will use in the next "
                        "part. It must be answerable with a single word: a "
                        "name for a new friend or place, a magical object, a "
                        "creature they meet, a treasure they find, a special "
                        "word. Make it feel like imagining together, never a "
                        "test. One short, friendly sentence.\n\n"
                        f"{story_so_far}"
                    ),
                },
            ],
            max_tokens=50,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return "What special thing should we add to our story next?"


_FILLER_WORDS = {
    "a", "an", "the", "is", "it", "was", "and", "to", "of", "in", "on",
    "um", "uh", "i", "think", "maybe", "said", "yes", "no",
}


def check_answer(
    client, question: str, answer: str, age_range: str | None = None
) -> tuple[bool, str]:
    """Decide whether the child's spoken answer can be used.

    Returns ``(fits, reply)``. When it fits, ``reply`` is one short sentence
    that repeats back what the child said, so the child knows Moo heard
    them. When it does not fit, ``reply`` gently asks the question again in
    simpler words. Silly and imaginative answers fit. Empty, filler only,
    cut off, off topic or unsafe answers do not. If the check itself fails,
    the answer is accepted so the story never stalls.
    """
    answer = (answer or "").strip()
    age = age_range or "3-5"
    if not answer:
        return False, ""
    try:
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You help Moo, a gentle storyteller for children aged "
                        f"{age}, listen to a child. The child's words come from "
                        "speech recognition and may contain small errors. Decide "
                        "if the answer responds to Moo's question in any way that "
                        "can be used in a gentle story. Silly, imaginative or very "
                        "short answers DO fit. The answer does NOT fit if it is "
                        "only filler sounds (um, uh, hmm), clearly cut off in the "
                        "middle, about something unrelated to the question, or not "
                        "safe for a young child. Reply as JSON with two keys: "
                        '"fits" (true or false) and "reply". If it fits, "reply" '
                        "is ONE short, happy sentence that repeats back what the "
                        "child said. If it does not fit, \"reply\" is ONE short, "
                        "kind sentence that asks the question again in simpler "
                        "words, never saying the child was wrong."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Moo asked: {question}\nThe child answered: {answer}",
                },
            ],
            max_tokens=80,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        return bool(data.get("fits", True)), str(data.get("reply", "")).strip()
    except Exception:
        logger.warning("answer check failed; accepting the answer", exc_info=True)
        return True, ""


def _word_length(age_range: str | None) -> str:
    """Letters in the word for each age range, so the word fits the child."""
    if age_range in ("0-3", "3-5"):
        return "3 or 4"
    return "4 to 6"


def _yes_no(client, instruction: str) -> bool | None:
    """Ask the model a yes or no question. None if the call fails."""
    try:
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": "Answer as JSON with one key, \"yes\", true or false.",
                },
                {"role": "user", "content": instruction},
            ],
            max_tokens=20,
        )
        return bool(json.loads(resp.choices[0].message.content or "{}").get("yes"))
    except Exception:
        logger.warning("yes/no check failed", exc_info=True)
        return None


_PRACTICE_WORDS = {
    "short": ["cat", "dog", "sun", "cow", "hat", "bed", "cup", "egg", "bus", "pig",
              "moon", "fish", "cake", "duck", "frog", "milk", "star", "boat", "tree", "ball"],
    "long": ["apple", "horse", "snail", "train", "cloud", "bread", "sheep", "flower",
             "rabbit", "garden", "candle", "pocket", "turtle", "basket", "banana", "rocket"],
}


def pick_practice_word(client, age_range: str | None = None, exclude: set[str] | None = None) -> str:
    """One simple, friendly word for the word games outside a story."""
    exclude = {w.lower() for w in (exclude or set())}
    try:
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": _system_prompt(age_range)},
                {
                    "role": "user",
                    "content": (
                        "Give ONE simple, friendly English word that a young child "
                        f"knows, about {_word_length(age_range)} letters long, a "
                        "complete real word and never a shortened one: an animal, "
                        "a food, a toy or something at home or outside. Only "
                        "letters A to Z."
                        + (" Do NOT use: " + ", ".join(sorted(exclude)) + "." if exclude else "")
                        + " Reply with ONLY the word."
                    ),
                },
            ],
            max_tokens=8,
        )
        tokens = re.findall(r"[A-Za-z]+", resp.choices[0].message.content or "")
        if tokens and tokens[0].lower() not in exclude:
            return tokens[0].lower()
    except Exception:
        logger.warning("pick_practice_word failed; using the word list", exc_info=True)
    pool = _PRACTICE_WORDS["short" if age_range in ("0-3", "3-5") else "long"]
    left = [w for w in pool if w not in exclude] or pool
    return random.choice(left)


# How speech recognition writes a spoken letter name. A fixed list is used
# here because the story models got only about half of these right when
# they were tested on 20 November 2026.
_LETTER_SOUNDS = {
    "a": "a ay eh hey aye",
    "b": "b bee be bea",
    "c": "c see sea cee si",
    "d": "d dee de",
    "e": "e ee he eat",
    "f": "f ef eff if",
    "g": "g gee jee ji",
    "h": "h aitch age each ach haitch",
    "i": "i eye aye hi",
    "j": "j jay jai",
    "k": "k kay okay ok kaye",
    "l": "l el ell elle",
    "m": "m em am",
    "n": "n en and in an",
    "o": "o oh owe",
    "p": "p pee pea",
    "q": "q queue cue kew",
    "r": "r are ar our",
    "s": "s ess yes es as",
    "t": "t tee tea",
    "u": "u you ew yu",
    "v": "v vee ve",
    "w": "w doubleyou doubleu dub",
    "x": "x ex eggs axe",
    "y": "y why wye",
    "z": "z zee zed",
}
_LETTER_FILLER = {
    "it", "its", "is", "the", "letter", "think", "that", "this", "thats", "um", "uh",
    "hmm", "maybe", "a", "an", "i", "oh", "one", "was",
}


def check_letter(client, letter: str, heard: str) -> bool:
    """Did the child name `letter`? Speech recognition often writes a letter
    as its name or a sound-alike (B as 'bee', S as 'ess' or 'yes'), so the
    answer is looked up in a fixed list. `client` is not used."""
    letter = letter.lower()
    sounds = set(_LETTER_SOUNDS.get(letter, letter).split())
    text = re.sub(r"[^a-z ]", "", (heard or "").lower().replace("-", " "))
    text = text.replace("double you", "doubleyou").replace("double u", "doubleu")
    words = text.split()
    if not words:
        return False
    # "B like ball", "S as in sun": the letter, or a word that starts with it.
    for i, w in enumerate(words[:-1]):
        if w in ("like", "for") or (w == "as" and words[i + 1] == "in"):
            example = words[-1]
            before = [x for x in words[:i] if x in sounds]
            if before or example.startswith(letter):
                return True
    # Drop filler words, but never one that is itself a sound of this letter.
    kept = [w for w in words if w in sounds or w not in _LETTER_FILLER]
    # What is left must be the letter and nothing else ("pee pee" is fine,
    # "I see a dog" is not).
    return 0 < len(kept) <= 2 and all(w in sounds for w in kept)


def make_riddle(client, word: str, age_range: str | None = None) -> str:
    """A short riddle whose answer is `word`, without saying the word."""
    try:
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": _system_prompt(age_range)},
                {
                    "role": "user",
                    "content": (
                        f"Make a very short, gentle riddle for the child whose "
                        f"answer is '{word}'. Two short sentences at most, end "
                        "with 'What am I?' or a similar question, and never "
                        "say the word itself. Reply with just the riddle."
                    ),
                },
            ],
            max_tokens=60,
        )
        text = resp.choices[0].message.content.strip()
        if text and word.lower() not in text.lower():
            return text
    except Exception:
        logger.warning("make_riddle failed", exc_info=True)
    return f"I am in our story, and I start with the letter {word[0].upper()}. What am I?"


def check_guess(client, word: str, heard: str) -> bool:
    """Did the child guess `word` (or something that clearly means it)?"""
    heard = (heard or "").strip()
    if not heard:
        return False
    if word.lower() in re.findall(r"[a-z]+", heard.lower()):
        return True
    verdict = _yes_no(
        client,
        f"A child answered a riddle whose answer is '{word}'. Speech "
        f'recognition heard: "{heard}". Is the answer the same thing as '
        f"'{word}', allowing plurals, small mishearings and synonyms?",
    )
    return bool(verdict)


def word_to_spell(answer: str, fallback_story: str) -> str:
    """Choose a single word to spell from the child's spoken answer.

    Takes the longest plain-alphabetic word (2-8 letters) that isn't a filler
    word. If the answer has nothing usable (silence, mumbling), falls back to
    a simple noun pulled from the story.
    """
    words = [w.lower() for w in re.findall(r"[A-Za-z]+", answer or "")]
    candidates = [
        w for w in words if 2 <= len(w) <= 8 and w not in _FILLER_WORDS
    ]
    if candidates:
        return max(candidates, key=len)
    return pick_spelling_word(fallback_story)


def _is_safe(client, text: str) -> bool:
    try:
        resp = client.moderations.create(model=MODERATION_MODEL, input=text)
        return not resp.results[0].flagged
    except Exception as exc:
        logger.warning("Moderation call failed (%s); treating as safe", exc)
        return True


_COMMON_NOUNS = {
    "cat", "dog", "cow", "moo", "owl", "fox", "bird", "frog", "duck", "bear",
    "tree", "leaf", "moon", "star", "sun", "fish", "sand", "rain", "snow",
    "wind", "rock", "rose", "lily", "pond", "lake", "boat", "kite", "ball",
    "book", "cake", "milk", "honey", "apple", "berry", "bunny", "puppy",
    "kitty", "horse", "pony", "lamb", "calf", "chick", "bee", "ant",
    "leaf", "seed", "frog", "turtle",
}
