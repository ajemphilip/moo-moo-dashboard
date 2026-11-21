"""Kitten TTS 2 in a process of its own.

Kitten TTS 2 needs newer libraries than the rest of Moo Local, so it lives
in its own Python environment (~/.moo-kitten2-venv) and Moo Local starts
this file with that Python.

One request per line on stdin, as JSON:
    {"repo": "KittenML/kitten-tts-2" or a small model, "text": ..., "voice": ..., "out": path}
It writes a 16 bit mono wav to "out" and answers one line on stdout:
{"ok": true, "seconds": ...} or {"ok": false, "error": ...}.
"""

from __future__ import annotations

import json
import os
import sys
import time
import wave

import numpy as np

FULL = "KittenML/kitten-tts-2"


def main() -> None:
    # Answers go to the real stdout; anything the libraries print goes to stderr.
    answers = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    def say(**kw) -> None:
        answers.write(json.dumps(kw) + "\n")
        answers.flush()

    from kittenml import KittenTTS

    models: dict[str, object] = {}

    def get(repo: str):
        if repo not in models:
            # The full model runs on the Mac's graphics processor through MLX.
            models[repo] = KittenTTS(repo, backend="mlx") if repo == FULL else KittenTTS(repo)
        return models[repo]

    say(ok=True, ready=True)
    for line in sys.stdin:
        try:
            job = json.loads(line)
            started = time.time()
            model = get(job["repo"])
            audio = np.asarray(model.generate(job["text"], voice=job["voice"]), dtype=np.float32).reshape(-1)
            pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
            with wave.open(job["out"], "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(int(model.sample_rate))
                wf.writeframes(pcm.tobytes())
            say(ok=True, seconds=round(time.time() - started, 1))
        except Exception as exc:  # keep serving the next request
            say(ok=False, error=repr(exc))


if __name__ == "__main__":
    main()
