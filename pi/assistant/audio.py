"""Microphone capture + Whisper STT + TTS playback.

Whisper runs in the cloud (OpenAI's audio.transcriptions endpoint) — local
Whisper would OOM on the Pi Zero 2W's 512 MB. TTS likewise uses OpenAI's
audio.speech endpoint and plays via sounddevice.
"""

from __future__ import annotations

import io
import logging
import tempfile
import wave
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000  # fallback only; real I/O uses the device's native rate
RECORD_SECONDS = 6.0
_FALLBACK_RATE = 48000


def _device_default_rate(index: Optional[int], kind: str) -> int:
    """Default sample rate of an audio device (kind: 'input'/'output').

    USB soundcards usually accept only their native rate, so we must match
    it rather than assume 16 kHz.
    """
    try:
        import sounddevice as sd

        info = sd.query_devices(index) if index is not None else sd.query_devices(kind=kind)
        return int(info["default_samplerate"])
    except Exception:
        return _FALLBACK_RATE


def _resample(samples, src_rate: int, dst_rate: int):
    """Linear-interpolation resample of an int16 mono array."""
    if src_rate == dst_rate:
        return samples
    import numpy as np

    n_dst = int(round(len(samples) * dst_rate / src_rate))
    if n_dst <= 0:
        return samples
    dst_x = np.linspace(0, len(samples) - 1, n_dst)
    return np.interp(dst_x, np.arange(len(samples)), samples).astype(np.int16)


def _clarity_db() -> float:
    """How much treble to add when Moo speaks, from audio.clarity_db in the
    configuration. 0 turns it off."""
    try:
        import config

        return float((config.load_config().get("audio") or {}).get("clarity_db", 12.0))
    except Exception:
        return 12.0


def _brighten(samples, rate: int, db: float):
    """Lift the treble of an int16 mono array so speech sounds less muffled
    through Moo's small speaker inside its case. The gain rises from nothing
    at 1.5 kHz to the full amount at 4 kHz and stays there, and the lowest
    notes are turned down a little. The result is scaled down if it would clip."""
    import numpy as np

    if db <= 0 or len(samples) < 256:
        return samples
    spec = np.fft.rfft(samples.astype(np.float32))
    freqs = np.fft.rfftfreq(len(samples), 1.0 / rate)
    ramp = np.clip((freqs - 1500.0) / 2500.0, 0.0, 1.0)
    # The case also makes the low notes boom: take a third as much off below
    # 250 Hz, fading out by 700 Hz.
    low = np.clip((700.0 - freqs) / 450.0, 0.0, 1.0)
    spec *= 10.0 ** ((db * ramp - db / 3.0 * low) / 20.0)
    out = np.fft.irfft(spec, n=len(samples))
    peak = float(np.max(np.abs(out))) if len(out) else 0.0
    if peak > 32000:
        out *= 32000.0 / peak
    return out.astype(np.int16)


def _boost_quiet(samples):
    """Scale a quiet recording up toward a healthy level for Whisper.

    USB mic preamps are weak — capture often peaks at only a few percent of
    full scale even with the hardware gain maxed. Reference the 99th
    percentile (so a stray click doesn't pin the gain low), scale toward a
    target, and cap the gain so near-silence isn't amplified into noise.
    """
    import numpy as np

    # Higher target + bigger ceiling because the mic sits inside the casing —
    # capture peaks are typically a few percent of full scale.
    target = 0.6
    max_gain = 25.0
    mag = np.abs(samples.astype(np.float64)).ravel()
    if mag.size == 0:
        return samples
    ref = float(np.percentile(mag, 99.0))
    if ref < 1.0:  # essentially silent — nothing worth boosting
        return samples
    gain = min(max_gain, target * 32767.0 / ref)
    if gain <= 1.0:  # already loud enough
        return samples
    logger.info("capture boosted x%.1f", gain)
    return np.clip(samples.astype(np.float64) * gain, -32768.0, 32767.0).astype(np.int16)


def _usb_device_indices() -> tuple[Optional[int], Optional[int]]:
    """Return (input, output) indices of the first USB audio device.

    Lets the assistant default to the USB soundcard's mic + speaker when the
    config has no explicit device set.
    """
    try:
        import sounddevice as sd
    except (ImportError, OSError):
        return None, None
    in_idx: Optional[int] = None
    out_idx: Optional[int] = None
    for idx, dev in enumerate(sd.query_devices()):
        name = str(dev.get("name", "")).upper()
        if "USB" not in name:
            continue
        if in_idx is None and dev.get("max_input_channels", 0) > 0:
            in_idx = idx
        if out_idx is None and dev.get("max_output_channels", 0) > 0:
            out_idx = idx
    return in_idx, out_idx


class AudioIO:
    def __init__(
        self,
        openai_client,
        tts_voice: str = "sage",
        input_device: Optional[int] = None,
        output_device: Optional[int] = None,
        dry_run: bool = False,
    ) -> None:
        self.client = openai_client
        self.tts_voice = tts_voice
        self.dry_run = dry_run

        # Default to the USB soundcard for whichever device the config
        # didn't pin explicitly.
        if not dry_run and (input_device is None or output_device is None):
            usb_in, usb_out = _usb_device_indices()
            if input_device is None:
                input_device = usb_in
            if output_device is None:
                output_device = usb_out
            logger.info("audio devices — input=%s output=%s", input_device, output_device)
        self.input_device = input_device
        self.output_device = output_device

    def record(self, seconds: float = RECORD_SECONDS) -> bytes:
        """Record from the configured input device and return WAV bytes."""
        if self.dry_run:
            logger.info("[dry-run] would record %.1fs of audio", seconds)
            return _silent_wav(seconds)

        import sounddevice as sd

        rate = _device_default_rate(self.input_device, "input")
        frames = int(seconds * rate)
        audio = sd.rec(
            frames,
            samplerate=rate,
            channels=1,
            dtype="int16",
            device=self.input_device,
        )
        sd.wait()
        audio = _boost_quiet(audio)
        # Whisper accepts any sample rate — it resamples server-side.
        return _to_wav_bytes(audio.tobytes(), rate)

    def play_cue(self, kind: str = "ready") -> None:
        """Play a short chime so the child knows when to talk / when to stop.

        "ready" — a rising three-note chime: *start speaking now*.
        "done"  — a falling two-note chime: *Moo has stopped listening*.
        """
        if self.dry_run:
            logger.info("[dry-run] cue tone: %s", kind)
            return
        import numpy as np
        import sounddevice as sd

        rate = _device_default_rate(self.output_device, "output")
        notes = (
            [(523, 0.16), (659, 0.16), (880, 0.34)]
            if kind == "ready"
            else [(880, 0.16), (523, 0.32)]
        )
        parts = []
        for freq, dur in notes:
            n = int(rate * dur)
            t = np.linspace(0, dur, n, endpoint=False)
            env = np.ones(n)
            fade = min(int(rate * 0.012), n // 2)
            if fade > 0:
                env[:fade] = np.linspace(0, 1, fade)
                env[-fade:] = np.linspace(1, 0, fade)
            parts.append(0.4 * np.sin(2 * np.pi * freq * t) * env)
        tone = np.concatenate(parts).astype(np.float32)
        try:
            sd.play(
                np.column_stack([tone, tone]),
                rate,
                device=self.output_device,
                latency="high",
            )
            sd.wait()
        except Exception:
            logger.warning("cue tone playback failed", exc_info=True)

    def record_until_silence(
        self,
        max_seconds: float = 30.0,
        silence_seconds: float = 3.0,
        start_timeout: float = 12.0,
    ) -> bytes:
        """Cue the child, capture until they stop talking, then cue again.

        How it works:
          * A rising chime tells the child to speak.
          * Audio is read in 100 ms blocks. The blocks from 0.2-0.6 s set an
            ambient noise floor (the chime has already finished).
          * The speech threshold is `ambient * 2.2 + 0.0015` (normalised RMS,
            0-1) — tuned low because the mic sits inside the casing and quiet
            voices barely cross the floor. Logged each call so it can be
            tuned further from journalctl.
          * Recording stops `silence_seconds` after the child goes quiet,
            is capped at `max_seconds`, and gives up after `start_timeout`
            if no speech is ever heard. A falling chime marks the end.
        """
        if self.dry_run:
            logger.info("[dry-run] would record until silence")
            return _silent_wav(2.0)

        import numpy as np
        import sounddevice as sd

        rate = _device_default_rate(self.input_device, "input")
        block = max(1, int(rate * 0.1))  # 100 ms blocks
        block_s = block / rate

        self.play_cue("ready")

        chunks: list = []
        ambient_samples: list[float] = []
        threshold = 0.02
        speech_started = False
        trailing_silence = 0.0
        elapsed = 0.0

        try:
            with sd.InputStream(
                samplerate=rate,
                channels=1,
                dtype="int16",
                device=self.input_device,
                blocksize=block,
            ) as stream:
                while elapsed < max_seconds:
                    data, _ = stream.read(block)
                    chunks.append(np.array(data, copy=True))
                    arr = data.astype(np.float64).ravel()
                    rms = (
                        float(np.sqrt(np.mean(arr * arr))) / 32768.0
                        if arr.size
                        else 0.0
                    )
                    elapsed += block_s

                    # 0.2-0.6 s window establishes the noise floor.
                    if elapsed <= 0.6:
                        if elapsed >= 0.2:
                            ambient_samples.append(rms)
                        continue
                    if ambient_samples and not speech_started and trailing_silence == 0.0:
                        ambient = sum(ambient_samples) / len(ambient_samples)
                        threshold = ambient * 2.2 + 0.0015
                        logger.info(
                            "record: ambient=%.4f threshold=%.4f", ambient, threshold
                        )
                        ambient_samples = []  # compute once

                    if rms > threshold:
                        speech_started = True
                        trailing_silence = 0.0
                    else:
                        trailing_silence += block_s

                    if not speech_started and elapsed >= start_timeout:
                        logger.info("record: no speech in %.1fs", start_timeout)
                        break
                    if speech_started and trailing_silence >= silence_seconds:
                        logger.info(
                            "record: speech ended (%.1fs trailing silence, %.1fs total)",
                            trailing_silence,
                            elapsed,
                        )
                        break
        except Exception:
            logger.exception("record_until_silence failed; returning what was captured")

        self.play_cue("done")

        if chunks:
            audio = np.concatenate(chunks)
        else:
            audio = np.zeros((0,), dtype=np.int16)
        audio = _boost_quiet(audio)
        return _to_wav_bytes(audio.tobytes(), rate)

    def transcribe(self, wav_bytes: bytes) -> str:
        """Send recorded audio to Whisper API; return transcript text."""
        if self.dry_run:
            logger.info("[dry-run] would transcribe via Whisper")
            return "puppy"

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(wav_bytes)
            tmp_path = Path(tmp.name)
        try:
            with tmp_path.open("rb") as f:
                resp = self.client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                )
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass
        return (resp.text or "").strip()

    def speak(self, text: str) -> None:
        """Generate TTS via OpenAI and play through the configured output device."""
        if not text:
            return
        if self.dry_run:
            logger.info("[dry-run] speak: %s", text[:80])
            return

        resp = self.client.audio.speech.create(
            model="tts-1",
            voice=self.tts_voice,
            input=text,
            response_format="wav",
        )
        wav_bytes = resp.read() if hasattr(resp, "read") else resp.content
        self._play_wav(wav_bytes)

    def _play_wav(self, wav_bytes: bytes) -> None:
        import numpy as np
        import sounddevice as sd

        src_rate, channels, audio = _read_wav(wav_bytes)
        if channels > 1:
            audio = audio.reshape(-1, channels).mean(axis=1).astype(np.int16)

        # OpenAI TTS is 24 kHz; USB soundcards usually want 44.1/48 kHz.
        dst_rate = _device_default_rate(self.output_device, "output")
        audio = _resample(audio, src_rate, dst_rate)
        audio = _brighten(audio, dst_rate, _clarity_db())

        # The USB sound card swallows the first moment of sound while it wakes
        # up, and can drop the very end. A short silence on both sides keeps
        # the first and last word whole.
        audio = np.concatenate([
            np.zeros(int(dst_rate * 0.25), dtype=np.int16), audio, np.zeros(int(dst_rate * 0.3), dtype=np.int16),
        ])

        # Most USB soundcards expect stereo out — duplicate the mono signal.
        stereo = np.column_stack([audio, audio])
        sd.play(stereo, dst_rate, device=self.output_device, latency="high")
        sd.wait()


def _read_wav(wav_bytes: bytes):
    """Rate, channels and 16 bit samples of a wav. Besides ordinary 16 bit
    wavs this reads the 32 bit float wavs a browser voice sends (format 3),
    which Python's wave module refuses."""
    import struct

    import numpy as np

    if wav_bytes[:4] != b"RIFF" or wav_bytes[8:12] != b"WAVE":
        raise ValueError("not a wav")
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(wav_bytes):
        cid, size = wav_bytes[pos:pos + 4], struct.unpack("<I", wav_bytes[pos + 4:pos + 8])[0]
        body = wav_bytes[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif cid == b"data":
            data = body
        pos += 8 + size + (size & 1)
    if fmt is None or data is None:
        raise ValueError("wav without fmt or data")
    tag, channels, rate, _, _, bits = fmt
    if tag == 0xFFFE:  # extensible: decide by bit depth
        tag = 3 if bits == 32 else 1
    if tag == 3 and bits == 32:
        audio = (np.clip(np.frombuffer(data, dtype="<f4"), -1, 1) * 32767).astype(np.int16)
    elif tag == 1 and bits == 16:
        audio = np.frombuffer(data[: len(data) // 2 * 2], dtype="<i2").astype(np.int16)
    else:
        raise ValueError(f"wav format {tag} with {bits} bits is not supported")
    return rate, channels, audio


def _to_wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def _silent_wav(seconds: float, sample_rate: int = SAMPLE_RATE) -> bytes:
    return _to_wav_bytes(b"\x00\x00" * int(seconds * sample_rate), sample_rate)
