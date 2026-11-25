"""Tell the parent whether the Moo joined a wifi.

When the Moo leaves its own wifi to join another, the phone or computer that
asked loses touch with it, so the page cannot say how it went. The Moo itself
does: it watches the join for up to 90 seconds, plays rising notes on its
speaker when it is on the new wifi, or two low notes when it gives up, and
keeps the result in the configuration (wifi.last_join). The dashboard shows a
failed join once the parent is back on the Moo's own wifi.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time

import config as moo_config

from . import wifi_manager

logger = logging.getLogger(__name__)

JOIN_TIMEOUT_SECONDS = 90
HOTSPOT_GRACE_SECONDS = 25   # comitup back on its own wifi after this long means it gave up
POLL_SECONDS = 2

_lock = threading.Lock()
_watching: threading.Thread | None = None


def _is_hotspot() -> bool:
    """True when wlan0 is the Moo's own access point again."""
    try:
        out = subprocess.run(
            ["/usr/sbin/iw", "dev", wifi_manager.WIFI_INTERFACE, "info"],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    return "type AP" in out


def _seen(ssid: str) -> bool | None:
    """Whether the network was in the Moo's last scan, or None if unknown."""
    cache = wifi_manager._scan_cache
    if not cache:
        return None
    return any(n.ssid == ssid for n in cache[1])


def failure_message(ssid: str, seen: bool | None) -> str:
    if seen is False:
        return (
            f"Moo could not see {ssid}. Moo only hears 2.4 GHz wifi: on an iPhone turn on Maximize Compatibility "
            "under Personal Hotspot and keep that screen open, then try again."
        )
    return (
        f"Moo did not join {ssid}. Check the password. For an iPhone hotspot, turn on Maximize Compatibility "
        "and keep the Personal Hotspot screen open while Moo joins."
    )


def _record(ssid: str, ok: bool, message: str, pending: bool = False) -> None:
    moo_config.update_config(
        {"wifi": {"last_join": {"ssid": ssid, "ok": ok, "message": message, "pending": pending}}}
    )


def chime(kind: str) -> None:
    """Rising notes for joined, two low notes for not joined. Never raises."""
    try:
        import numpy as np
        from assistant.audio import _play_array, _usb_device_indices

        import sounddevice as sd

        device = (moo_config.load_config().get("audio") or {}).get("output_index")
        if device is None:
            device = _usb_device_indices()[1]
        info = dict(sd.query_devices(device, "output") if device is not None else sd.query_devices(kind="output"))
        rate = int(info["default_samplerate"])
        notes = [523.25, 659.25, 783.99, 1046.5] if kind == "joined" else [392.0, 261.63]
        length = 0.22 if kind == "joined" else 0.45

        parts = []
        for f in notes:
            n = int(rate * length)
            t = np.linspace(0, length, n, endpoint=False)
            s = 0.4 * np.sin(2 * np.pi * f * t)
            fade = min(800, n // 6)
            s[:fade] *= np.linspace(0.0, 1.0, fade)
            s[-fade:] *= np.linspace(1.0, 0.0, fade)
            parts += [s, np.zeros(int(rate * 0.06))]
        seq = np.concatenate(parts).astype(np.float32)
        channels = 2 if int(info.get("max_output_channels", 2) or 2) >= 2 else 1
        frames = np.column_stack([seq] * channels)
        _play_array(frames, rate, device)
    except Exception:
        logger.exception("Could not play the %s chime", kind)


def _watch(ssid: str, seen: bool | None) -> None:
    start = time.monotonic()
    while time.monotonic() - start < JOIN_TIMEOUT_SECONDS:
        time.sleep(POLL_SECONDS)
        if wifi_manager.current_ssid() == ssid:
            logger.info("Joined %s", ssid)
            _record(ssid, True, f"Moo joined {ssid}.")
            chime("joined")
            return
        if time.monotonic() - start > HOTSPOT_GRACE_SECONDS and _is_hotspot():
            break
    logger.info("Did not join %s", ssid)
    _record(ssid, False, failure_message(ssid, seen))
    chime("failed")


def watch(ssid: str) -> None:
    """Start watching a join that was just asked of comitup."""
    global _watching
    seen = _seen(ssid)
    _record(ssid, False, f"Moo is joining {ssid}...", pending=True)
    with _lock:
        _watching = threading.Thread(target=_watch, args=(ssid, seen), name="wifi-join", daemon=True)
        _watching.start()


def last_join() -> dict | None:
    """The last join, with joining=True while it is still being watched."""
    result = (moo_config.load_config().get("wifi") or {}).get("last_join")
    if not result:
        return None
    joining = bool(_watching and _watching.is_alive())
    if result.get("pending") and not joining:
        # The setup service restarted during the join, so the outcome is unknown.
        result = {**result, "message": f"Moo restarted before it knew whether it joined {result.get('ssid')}."}
    return {**result, "joining": joining}
