"""Letter-practice exercise endpoints.

POST /letters/start  — open a session (holds the Braille driver alive)
POST /letters/show   — raise one letter on the cell; the dots stay up
GET  /letters/state  — current letter / phase (the phone polls this)
POST /letters/stop   — close the session

The parent picks a letter on the phone; the Moo raises that letter's dots
and leaves them up — there is no timer. The child feels the letter, then
presses the physical buttons to lower the dots one by one (the driver
lowers motor N when button N is pressed), clearing the letter by hand.
"""

from __future__ import annotations

import logging
import threading
import time

from fastapi import APIRouter, HTTPException

from assistant.braille_letters import dots_for

from ..models import LetterShowRequest, LetterState, SimpleStatus
from ..services import braille_hw, service_control

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/letters")

_OWNER = "letters"

_lock = threading.Lock()
_active = False
_was_running = False
_letter: str | None = None
_phase = "idle"                  # idle | showing
_rendering = threading.Event()   # set while the cell is being raised


def _snapshot() -> LetterState:
    driver = braille_hw.acquire(_OWNER) if _active else None
    return LetterState(
        active=_active,
        dry_run=bool(getattr(driver, "dry_run", False)) if driver else False,
        letter=_letter,
        dots=list(dots_for(_letter)) if _letter else [],
        phase=_phase,
        repeats=0,
    )


def _render_letter(letter: str) -> None:
    """Raise this letter's dots and lower every other dot — then leave it up.

    No hold, no timed lowering: the dots stay raised until the child clears
    them with the buttons or the parent shows another letter.
    """
    global _phase
    if _rendering.is_set():
        return
    _rendering.set()
    try:
        driver = braille_hw.acquire(_OWNER)
        with _lock:
            _phase = "showing"
        driver.render_letter(letter)
    except Exception:
        logger.exception("letter render failed")
    finally:
        with _lock:
            _phase = "idle"
        _rendering.clear()


@router.post("/start", response_model=LetterState)
def letters_start() -> LetterState:
    global _active, _was_running, _letter, _phase
    with _lock:
        if not _active:
            _was_running = service_control.is_assistant_running()
            if _was_running:
                service_control.stop_assistant()
                time.sleep(1.0)
            try:
                braille_hw.acquire(_OWNER)
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            _active = True
            _letter = None
            _phase = "idle"
    return _snapshot()


@router.post("/show", response_model=LetterState)
def letters_show(req: LetterShowRequest) -> LetterState:
    global _letter
    if not _active:
        raise HTTPException(status_code=409, detail="Letter exercise not started")
    if _rendering.is_set():
        raise HTTPException(status_code=409, detail="Still raising the last letter")
    with _lock:
        _letter = req.letter.upper()
    threading.Thread(target=_render_letter, args=(_letter,), daemon=True).start()
    return _snapshot()


@router.get("/state", response_model=LetterState)
def letters_state() -> LetterState:
    return _snapshot()


@router.post("/stop", response_model=SimpleStatus)
def letters_stop() -> SimpleStatus:
    global _active, _letter, _phase
    with _lock:
        if not _active:
            return SimpleStatus(status="stopped")
    braille_hw.release(_OWNER)
    with _lock:
        _active = False
        _letter = None
        _phase = "idle"
    if _was_running:
        service_control.start_assistant()
    return SimpleStatus(status="stopped")
