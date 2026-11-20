"""Braille hardware endpoints.

POST /braille/test               — pulse one or all motors (quick wiring check)
POST /braille/calibrate/start    — open a calibration session (persistent driver)
POST /braille/calibrate/pulse    — pulse one motor during calibration
GET  /braille/calibrate/button   — report the last button press (for mapping)
POST /braille/calibrate/save     — write calibrated pin map to config, close session
POST /braille/calibrate/stop     — close the session without saving

A calibration session holds one GpioBrailleDriver alive (module-level) so the
buttons keep firing events while the wizard polls. The assistant is paused for
the duration.
"""

from __future__ import annotations

import logging
import threading
import time

from fastapi import APIRouter, HTTPException

import config as moo_config

from ..models import (
    BrailleTestRequest,
    BrailleTestResponse,
    CalibrateButtonResponse,
    CalibrateStartResponse,
    CalibrationSaveRequest,
    MotorPulseRequest,
    SimpleStatus,
)
from ..services import service_control

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/braille")

# Calibration session state — one driver held alive for the wizard.
_calib_lock = threading.Lock()
_calib_driver = None  # GpioBrailleDriver | None
_calib_was_running = False


@router.post("/test", response_model=BrailleTestResponse)
def test(req: BrailleTestRequest) -> BrailleTestResponse:
    from assistant.braille import make_driver

    was_running = service_control.is_assistant_running()
    if was_running:
        service_control.stop_assistant()
        time.sleep(1.0)

    started = time.monotonic()
    cfg = moo_config.load_config()
    driver = make_driver(cfg, dry_run=False)
    is_dry = getattr(driver, "dry_run", False)

    try:
        if req.motor_index is None:
            tested = driver.test_all_motors(hold_seconds=1.0)  # type: ignore[attr-defined]
        else:
            driver.test_motor(req.motor_index, hold_seconds=1.0)  # type: ignore[attr-defined]
            tested = [req.motor_index]
    except Exception as exc:
        logger.exception("Braille test failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        try:
            driver.close()
        except Exception:
            pass
        if was_running:
            service_control.start_assistant()

    duration = time.monotonic() - started
    return BrailleTestResponse(
        status="dry_run" if is_dry else "ok",
        motors_tested=tested,
        duration_seconds=round(duration, 2),
    )


@router.post("/calibrate/start", response_model=CalibrateStartResponse)
def calibrate_start() -> CalibrateStartResponse:
    global _calib_driver, _calib_was_running
    from assistant.braille import make_driver

    with _calib_lock:
        if _calib_driver is None:
            _calib_was_running = service_control.is_assistant_running()
            if _calib_was_running:
                service_control.stop_assistant()
                time.sleep(1.0)
            _calib_driver = make_driver(moo_config.load_config(), dry_run=False)

        return CalibrateStartResponse(
            status="started",
            dry_run=getattr(_calib_driver, "dry_run", False),
            motor_pins=[list(p) for p in _calib_driver.pins.motor_pins],
            button_pins=list(_calib_driver.pins.button_pins),
        )


@router.post("/calibrate/pulse", response_model=SimpleStatus)
def calibrate_pulse(req: MotorPulseRequest) -> SimpleStatus:
    with _calib_lock:
        if _calib_driver is None:
            raise HTTPException(status_code=409, detail="Calibration not started")
        try:
            if req.direction == "up":
                _calib_driver.raise_motor(req.motor_index)  # type: ignore[attr-defined]
            elif req.direction == "down":
                _calib_driver.lower_motor(req.motor_index)  # type: ignore[attr-defined]
            else:
                _calib_driver.test_motor(req.motor_index, hold_seconds=1.0)  # type: ignore[attr-defined]
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    return SimpleStatus(status="pulsed")


@router.get("/calibrate/button", response_model=CalibrateButtonResponse)
def calibrate_button() -> CalibrateButtonResponse:
    with _calib_lock:
        if _calib_driver is None:
            raise HTTPException(status_code=409, detail="Calibration not started")
        button = _calib_driver.pop_last_button()  # type: ignore[attr-defined]
    return CalibrateButtonResponse(button=button)


@router.post("/calibrate/save", response_model=SimpleStatus)
def calibrate_save(req: CalibrationSaveRequest) -> SimpleStatus:
    global _calib_driver
    with _calib_lock:
        moo_config.update_config(
            {"braille": {"gpio": {
                "motor_pins": [list(p) for p in req.motor_pins],
                "button_pins": list(req.button_pins),
            }}}
        )
        _close_calib_driver()
    if _calib_was_running:
        service_control.start_assistant()
    return SimpleStatus(status="saved")


@router.post("/calibrate/stop", response_model=SimpleStatus)
def calibrate_stop() -> SimpleStatus:
    with _calib_lock:
        _close_calib_driver()
    if _calib_was_running:
        service_control.start_assistant()
    return SimpleStatus(status="stopped")


def _close_calib_driver() -> None:
    """Close + clear the calibration driver. Caller must hold _calib_lock.

    Lowers every dot first — calibration pulses motors and can leave dots
    raised, so the cell is reset flat before the driver is released.
    """
    global _calib_driver
    if _calib_driver is not None:
        try:
            _calib_driver.reset_cell()
        except Exception:
            logger.warning("calib reset_cell raised", exc_info=True)
        try:
            _calib_driver.close()
        except Exception:
            logger.warning("calib driver close raised", exc_info=True)
        _calib_driver = None
