"""POST /restart, POST /reset."""

from __future__ import annotations

from fastapi import APIRouter

import config as moo_config

from ..models import SimpleStatus
from ..services import service_control, wifi_manager

router = APIRouter()


@router.post("/restart", response_model=SimpleStatus)
def restart() -> SimpleStatus:
    service_control.restart_assistant()
    return SimpleStatus(status="restarting")


@router.post("/reset", response_model=SimpleStatus)
def reset() -> SimpleStatus:
    service_control.stop_assistant()
    moo_config.reset_config()
    wifi_manager.reset_to_hotspot()
    return SimpleStatus(status="resetting")
