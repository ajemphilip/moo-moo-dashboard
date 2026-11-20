"""Voice assistant entry point.

Run as `python -m assistant` from the venv. Reads /etc/moo/config.json on
startup; exits 78 (configuration error) if missing so systemd's
ConditionPathExists gating cleanly stops further restart attempts.

Loop: intro → ask theme → record + transcribe → story (moderated) → spelling
prompt → for each letter, drive the Pi-direct GPIO Braille driver (TB6612 +
MCP23017 + MPR121) → praise + extend story → loop. Errors in the loop are
logged and the loop sleeps 5s before retrying.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time

import config as moo_config

from .audio import AudioIO
from .braille import BrailleDriver, make_driver
from .story import (
    extend_story,
    generate_intro,
    generate_story,
    pick_spelling_word,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("moo.assistant")

EXIT_CONFIG_ERROR = 78


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Stub GPIO + audio I/O")
    args = parser.parse_args()

    cfg = moo_config.load_config()
    if not moo_config.is_configured():
        logger.error("No /etc/moo/config.json — exiting (78). Run setup wizard first.")
        return EXIT_CONFIG_ERROR

    prefs = cfg.get("preferences", {})
    audio_cfg = cfg.get("audio", {})

    _apply_alsa_volume(prefs.get("volume"))

    try:
        import openai
    except ImportError:
        logger.error("openai not installed")
        return 1
    from assistant.ai_client import ai_mode, make_client

    client = make_client(cfg)
    logger.info("AI engine: %s", ai_mode(cfg))

    audio = AudioIO(
        client,
        tts_voice=prefs.get("tts_voice", "sage"),
        input_device=audio_cfg.get("input_index"),
        output_device=audio_cfg.get("output_index"),
        dry_run=args.dry_run,
    )
    braille = make_driver(cfg, dry_run=args.dry_run)

    age_range = prefs.get("age_range", "3-5")

    try:
        _run_loop(client, audio, braille, age_range)
    finally:
        braille.close()
    return 0


def _run_loop(client, audio: AudioIO, braille: BrailleDriver, age_range: str) -> None:
    while True:
        try:
            intro = generate_intro(client, age_range)
            audio.speak(intro)

            theme_audio = audio.record(seconds=6.0)
            theme = audio.transcribe(theme_audio) or "a friendly cow"
            logger.info("Child picked theme: %s", theme)

            story = generate_story(client, theme, age_range)
            audio.speak(story)

            word = pick_spelling_word(story)
            audio.speak(
                f"Let's spell the word {word} together! Listen and feel each letter."
            )
            braille.send_word(word)

            audio.speak("Wonderful job! Here's a little more of our story.")
            extension = extend_story(client, story, "", "", age_range)
            audio.speak(extension)

            audio.speak("Would you like another story?")
            time.sleep(2.0)
        except KeyboardInterrupt:
            logger.info("Interrupted; exiting")
            return
        except Exception as exc:
            logger.exception("Loop error: %s — retrying in 5s", exc)
            time.sleep(5.0)


def _apply_alsa_volume(pct: int | None) -> None:
    if pct is None:
        return
    pct = max(0, min(100, int(pct)))
    try:
        subprocess.run(
            ["amixer", "set", "Master", f"{pct}%"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        logger.info("amixer not installed; skipping volume set")


if __name__ == "__main__":
    sys.exit(main())
