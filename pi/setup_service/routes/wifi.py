"""GET /wifi/scan, POST /wifi/connect, GET /wifi/join."""

from __future__ import annotations

import logging
import subprocess

from fastapi import APIRouter, HTTPException

import config as moo_config

from ..models import WifiConnectRequest, WifiConnectResponse, WifiNetwork, WifiScanResponse
from ..services import wifi_join, wifi_manager
from . import network

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/wifi")


@router.get("/scan", response_model=WifiScanResponse)
def scan() -> WifiScanResponse:
    nets = wifi_manager.scan_networks()
    return WifiScanResponse(
        networks=[WifiNetwork(ssid=n.ssid, signal=n.signal, secured=n.secured) for n in nets]
    )


@router.post("/connect", response_model=WifiConnectResponse)
def connect(req: WifiConnectRequest) -> WifiConnectResponse:
    try:
        wifi_manager.connect_to_network(req.ssid, req.password)
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not connect to {req.ssid}: {exc.stderr.strip() or 'comitup error'}",
        ) from exc
    # The Moo reports how the join went: a sound now, and GET /wifi/join later.
    wifi_join.watch(req.ssid)
    # Joining a wifi ends offline use: port 80 goes back to comitup.
    if moo_config.is_offline():
        moo_config.update_config({"network": {"offline": False}})
        network.apply_redirect(False)
    return WifiConnectResponse()


@router.get("/join")
def last_join() -> dict:
    """How the last join went: {ssid, ok, message, joining}, or {} if none."""
    return wifi_join.last_join() or {}
