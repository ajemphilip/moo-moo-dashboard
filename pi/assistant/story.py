"""GPT-4 storytelling with a moderation gate.

Every generated story passes through OpenAI's moderation endpoint before TTS.
If flagged, regenerate up to twice with a stricter prompt; on third failure
fall back to a static safe story so the experience never blocks on safety.
"""

from __future__ import annotations

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


def _system_prompt(age_range: str | None, strict: bool = False) -> str:
    age = age_range or "3-5"
    base = (
        "You are Moo, a gentle storyteller for young children aged "
        f"{age}. Tell warm, simple stories using short sentences and concrete "
        "imagery. Sound cheerful, warm, and gently excited — playful, never "
        "flat. Never include violence, scary content, or anything "
        "frightening. Keep each part very short — about 2 to 4 simple "
        "sentences, roughly 60 words, so it reads aloud in 20 to 30 seconds."
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
                        "continue. About 2 to 4 short sentences." + _BLANK_INSTRUCTION
                    ),
                },
            ],
            max_tokens=160,
        )
        story = resp.choices[0].message.content.strip()
        if _is_safe(client, story):
            return story
        logger.warning("Story flagged by moderation (attempt %d), retrying strictly", attempt + 1)

    logger.warning("All moderation retries exhausted; using fallback story")
    return SAFE_FALLBACK_STORY


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
                        " About 2 to 4 short sentences." + _BLANK_INSTRUCTION
                    ),
                },
            ],
            max_tokens=160,
        )
        extension = resp.choices[0].message.content.strip()
        if _is_safe(client, extension):
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
                        "and simple and short, 3 to 7 letters, easy for a "
                        "young child." + avoid + " Reply with ONLY that single "
                        f"word.\n\n{story_so_far}"
                    ),
                },
            ],
            max_tokens=10,
        )
        tokens = re.findall(r"[A-Za-z]+", resp.choices[0].message.content or "")
        if tokens and tokens[0].lower() not in exclude:
            return tokens[0].lower()
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
