"""Every phone, browser and computer that has worked for this Moo, remembered.

Lent devices (the Moo app, This device in a browser) are remembered when they
say hello; computers with Moo Local when the Moo finds them on the network or
one is chosen. Each is kept in the configuration under `known_devices` by its
name, with its kind, when it was first and last seen and, for a computer, its
address. A device that is off stays on the list, so it can be chosen again
once it is back on the same network.

The SD card is spared: while a device keeps working, its last-seen time is
written at most every five minutes.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import config as moo_config

KEY = "known_devices"
WRITE_EVERY = 300.0

_lock = threading.Lock()
_last_write: dict[str, float] = {}


def remember(name: str, kind: str, *, url: str | None = None, device: str | None = None, force: bool = False) -> None:
    """Note that a device was seen now. `kind` is "lent" or "computer"."""
    name = (name or "").strip()
    if not name:
        return
    now = time.time()
    with _lock:
        if not force and now - _last_write.get(name, 0.0) < WRITE_EVERY:
            return
        _last_write[name] = now
        cfg = moo_config.load_config()
        known = dict(cfg.get(KEY) or {})
        entry = dict(known.get(name) or {})
        entry.setdefault("first_seen", now)
        entry["kind"] = kind
        entry["last_seen"] = now
        if url:
            entry["url"] = url.rstrip("/")
        if device:
            entry["device"] = device[:120]
        known[name] = entry
        cfg[KEY] = known
        moo_config.save_config(cfg)


def known() -> dict[str, dict[str, Any]]:
    """All remembered devices by name."""
    try:
        return dict(moo_config.load_config().get(KEY) or {})
    except Exception:
        return {}


def forget(name: str) -> bool:
    """Take a device off the list. Returns whether it was there."""
    with _lock:
        cfg = moo_config.load_config()
        known_now = dict(cfg.get(KEY) or {})
        if name not in known_now:
            return False
        del known_now[name]
        cfg[KEY] = known_now
        moo_config.save_config(cfg)
        _last_write.pop(name, None)
        return True
