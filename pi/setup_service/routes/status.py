"""GET /status, GET /logs."""

from __future__ import annotations

import socket
import time

from fastapi import APIRouter, HTTPException, Query

import config as moo_config

from ..models import LogsResponse, StatusResponse
from ..services import service_control, wifi_manager

router = APIRouter()
VERSION = "0.1.0"
_BOOT_TIME = time.monotonic()


@router.get("/status", response_model=StatusResponse)
def get_status() -> StatusResponse:
    cfg = moo_config.load_config()
    ssid = wifi_manager.current_ssid()
    return StatusResponse(
        configured=moo_config.is_configured(),
        wifi_connected=ssid is not None,
        wifi_ssid=ssid,
        assistant_running=service_control.is_assistant_running(),
        device_id=cfg.get("device", {}).get("id", _device_id_fallback()),
        version=VERSION,
        uptime_seconds=int(time.monotonic() - _BOOT_TIME),
        address=_wifi_address() if ssid else None,
    )


def _wifi_address() -> str | None:
    """The Moo's own address on the network, from the route it would use to reach
    the outside (nothing is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return None


@router.get("/logs", response_model=LogsResponse)
def get_logs(
    service: str = Query("assistant", pattern="^(setup|assistant)$"),
    lines: int = Query(200, ge=1, le=500),
) -> LogsResponse:
    unit = f"moo-{service}.service"
    try:
        log_lines = service_control.journal_lines(unit, lines=lines)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return LogsResponse(service=service, lines=log_lines)


def _device_id_fallback() -> str:
    try:
        with open("/sys/class/net/wlan0/address", "r", encoding="utf-8") as f:
            mac = f.read().strip().replace(":", "")
        return f"moo-{mac[-4:]}"
    except OSError:
        return "moo-unknown"
