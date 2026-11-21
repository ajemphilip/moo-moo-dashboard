"""Speaker + microphone test helpers for the audio setup wizard step.

play_tone     — a short pleasant arpeggio so the user can confirm the speaker.
record_and_measure — record a few seconds, return the peak level.
play_back     — echo a recording back so the user hears their own voice.

USB soundcards usually only support their native rate (44.1k/48k), so every
call resolves the device's default sample rate rather than assuming one.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

HEARD_THRESHOLD = 0.02  # peak amplitude (0..1) above which the mic counts as live
_FALLBACK_RATE = 48000


def _device_info(index: int | None, kind: str) -> dict:
    """Return a device's info dict (kind: 'input' or 'output')."""
    import sounddevice as sd

    if index is not None:
        return dict(sd.query_devices(index))
    return dict(sd.query_devices(kind=kind))


def _device_rate(index: int | None, kind: str) -> int:
    """Return a device's default sample rate."""
    try:
        return int(_device_info(index, kind)["default_samplerate"])
    except Exception:
        return _FALLBACK_RATE


def play_tone(output_index: int | None) -> None:
    """Play two clear, steady tones (low then high) on the output device.

    A sustained tone makes audio problems obvious — a clean tone is rock
    steady; underruns or a bad clock make it warble. `latency='high'` asks
    PortAudio for big buffers so a slow Pi doesn't starve the stream.
    """
    import numpy as np
    import sounddevice as sd

    info = _device_info(output_index, "output")
    rate = int(info["default_samplerate"])
    stereo = int(info.get("max_output_channels", 2) or 2) >= 2

    def tone(freq: float, dur: float):
        n = int(rate * dur)
        t = np.linspace(0, dur, n, endpoint=False)
        s = 0.4 * np.sin(2 * np.pi * freq * t)
        fade = min(1500, n // 8)
        s[:fade] *= np.linspace(0.0, 1.0, fade)
        s[-fade:] *= np.linspace(1.0, 0.0, fade)
        return s.astype(np.float32)

    gap = np.zeros(int(rate * 0.25), dtype=np.float32)
    seq = np.concatenate([tone(440.0, 1.2), gap, tone(587.33, 1.2)])
    if stereo:
        seq = np.column_stack([seq, seq])
    from assistant.audio import _play_array

    _play_array(seq if seq.ndim == 2 else seq.reshape(-1, 1), rate, output_index)


def record_and_measure(input_index: int | None, seconds: float = 3.0):
    """Record mono audio from the input device. Return (recording, rate, peak)."""
    import numpy as np
    import sounddevice as sd

    rate = _device_rate(input_index, "input")
    frames = int(seconds * rate)
    rec = sd.rec(
        frames, samplerate=rate, channels=1, dtype="float32", device=input_index
    )
    sd.wait()
    peak = float(np.max(np.abs(rec))) if rec.size else 0.0
    return rec, rate, peak


def play_back(recording, rate: int, output_index: int | None) -> None:
    """Play a recording back, amplified so a quiet mic is still audible.

    USB mic preamps capture at only a few percent of full scale, so a raw
    playback would be near-inaudible and look like a failure. Normalize
    toward a healthy level, capped so a near-silent recording isn't blown
    up into loud noise.
    """
    import numpy as np
    import sounddevice as sd

    rec = np.asarray(recording, dtype=np.float32)
    peak = float(np.max(np.abs(rec))) if rec.size else 0.0
    if peak > 1e-4:
        rec = np.clip(rec * min(20.0, 0.7 / peak), -1.0, 1.0)
    from assistant.audio import _play_array

    mono = rec.reshape(-1)
    _play_array(np.ascontiguousarray(np.column_stack([mono, mono])), rate, output_index)
