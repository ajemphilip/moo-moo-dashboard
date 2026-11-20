"""Single-owner Braille driver shared by the letter + story sessions.

Only one GpioBrailleDriver can hold the GPIO lines at a time. The letter
exercise and the storytelling session both need it, so they acquire it
through this helper: whoever asks first owns it, and a second caller gets a
clear error instead of the driver silently falling back to dry-run on a
"GPIO busy" failure.

Calibration keeps its own driver (see routes/braille.py) — the parent runs
calibration once at setup, long before using letters or stories.
"""

from __future__ import annotations

import logging
import threading

import config as moo_config

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_driver = None  # GpioBrailleDriver | None
_owner: str | None = None

# Sessions that give the cell up when another one asks for it. The button
# check is a diagnostic and can be left open by accident.
YIELDING_OWNERS = {"buttons"}


def acquire(owner: str):
    """Return the shared driver, creating it for `owner` if free.

    Raises RuntimeError if another session already owns the hardware.
    """
    global _driver, _owner
    from assistant.braille import make_driver

    with _lock:
        if _driver is not None and _owner != owner and _owner in YIELDING_OWNERS:
            # A button check left open (phone locked, tab closed) must never
            # block a story or letter practice. Hand the cell over.
            logger.info("braille hardware taken from %s by %s", _owner, owner)
            try:
                _driver.close()
            except Exception:
                logger.warning("braille driver close raised", exc_info=True)
            _driver = None
            _owner = None
        if _driver is not None and _owner != owner:
            raise RuntimeError(f"Braille hardware is busy ({_owner} session)")
        if _driver is None:
            _driver = make_driver(moo_config.load_config(), dry_run=False)
            _owner = owner
            logger.info("braille hardware acquired by %s", owner)
        return _driver


def release(owner: str) -> None:
    """Close + clear the driver if `owner` holds it. No-op otherwise."""
    global _driver, _owner
    with _lock:
        if _owner != owner or _driver is None:
            return
        try:
            _driver.close()
        except Exception:
            logger.warning("braille driver close raised", exc_info=True)
        logger.info("braille hardware released by %s", owner)
        _driver = None
        _owner = None


def owner() -> str | None:
    with _lock:
        return _owner
