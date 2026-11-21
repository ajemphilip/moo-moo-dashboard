"""Orpheus in a process of its own.

Moo Local starts this once and keeps it running. Orpheus used the Mac's GPU
inside the same process as Whisper, and the two together made the GPU time
out. Here it has its own GPU context.

One request per line on stdin, as JSON: {"text": ..., "voice": ..., "out": path}.
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

MODEL = os.environ.get("MOO_LOCAL_ORPHEUS", "mlx-community/orpheus-3b-0.1-ft-4bit")


def main() -> None:
    # Answers go to the real stdout; anything the libraries print goes to stderr.
    answers = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    def say(**kw) -> None:
        answers.write(json.dumps(kw) + "\n")
        answers.flush()

    from mlx_audio.tts.utils import load_model

    model = load_model(MODEL)
    say(ok=True, ready=True)
    for line in sys.stdin:
        try:
            job = json.loads(line)
            started = time.time()
            chunks, rate = [], 24000
            for r in model.generate(text=job["text"], voice=job["voice"], verbose=False):
                chunks.append(np.array(r.audio))
                rate = r.sample_rate
            audio = np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(1, np.float32)
            pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
            with wave.open(job["out"], "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(rate)
                wf.writeframes(pcm.tobytes())
            say(ok=True, seconds=round(time.time() - started, 1))
        except Exception as exc:  # keep serving the next request
            say(ok=False, error=repr(exc))


if __name__ == "__main__":
    main()
