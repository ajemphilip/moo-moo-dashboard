"""The MLX voices (Orpheus, Qwen3-TTS, Kitten) in a process of their own.

Moo Local starts this once and keeps it running. Orpheus used the Mac's GPU
inside the same process as Whisper, and the two together made the GPU time
out. Here the voices have their own GPU context.

One request per line on stdin, as JSON:
    {"engine": "orpheus" | "qwen3" | "kitten", "text": ..., "voice": ..., "out": path}
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

REPOS = {
    "orpheus": os.environ.get("MOO_LOCAL_ORPHEUS", "mlx-community/orpheus-3b-0.1-ft-4bit"),
    "qwen3": os.environ.get("MOO_LOCAL_QWEN3", "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"),
    "kitten": os.environ.get("MOO_LOCAL_KITTEN", "mlx-community/kitten-tts-mini-0.8"),
}
BIG = {"orpheus", "qwen3"}  # only one of these stays in memory
STORYTELLER = "Read it like a warm, playful storyteller reading to a young child, with wonder and excitement."


def main() -> None:
    # Answers go to the real stdout; anything the libraries print goes to stderr.
    answers = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    def say(**kw) -> None:
        answers.write(json.dumps(kw) + "\n")
        answers.flush()

    # Kitten turns text into sounds with espeak; a copy comes with Kokoro.
    try:
        import espeakng_loader

        os.environ.setdefault("PHONEMIZER_ESPEAK_LIBRARY", espeakng_loader.get_library_path())
        os.environ.setdefault("ESPEAK_DATA_PATH", espeakng_loader.get_data_path())
    except Exception:
        pass

    from mlx_audio.tts.utils import load_model

    models: dict[str, object] = {}

    def get(engine: str):
        if engine not in models:
            if engine in BIG:
                for other in BIG & set(models):
                    del models[other]
                import gc

                import mlx.core as mx

                gc.collect()
                mx.clear_cache()
            models[engine] = load_model(REPOS[engine])
        return models[engine]

    say(ok=True, ready=True)
    for line in sys.stdin:
        try:
            job = json.loads(line)
            engine = job.get("engine", "orpheus")
            started = time.time()
            model = get(engine)
            if engine == "qwen3":
                results = model.generate_custom_voice(
                    text=job["text"], speaker=job["voice"], language="English",
                    instruct=STORYTELLER if job.get("storyteller", True) else None,
                )
            else:
                results = model.generate(text=job["text"], voice=job["voice"], verbose=False)
            chunks, rate = [], 24000
            for r in results:
                chunks.append(np.array(r.audio).reshape(-1))
                rate = r.sample_rate
            audio = np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(1, np.float32)
            pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
            with wave.open(job["out"], "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(int(rate))
                wf.writeframes(pcm.tobytes())
            say(ok=True, seconds=round(time.time() - started, 1))
        except Exception as exc:  # keep serving the next request
            say(ok=False, error=repr(exc))


if __name__ == "__main__":
    main()
