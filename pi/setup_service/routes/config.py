"""GET/POST /config."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

import config as moo_config

from ..models import ConfigUpdateRequest
from ..services import openai_test, service_control, wifi_manager

router = APIRouter()


@router.get("/config")
def get_config() -> dict[str, Any]:
    return moo_config.redact(moo_config.load_config())


@router.post("/config")
def post_config(req: ConfigUpdateRequest) -> dict[str, Any]:
    partial = req.model_dump(exclude_none=True)

    if "openai_api_key" in partial:
        valid, error = openai_test.validate_api_key(partial["openai_api_key"])
        if not valid:
            raise HTTPException(status_code=400, detail=error or "Invalid API key")

    ssid = wifi_manager.current_ssid()
    if ssid:
        partial.setdefault("wifi", {})
        partial["wifi"].update({"ssid": ssid, "configured": True})

    partial["configured_at"] = datetime.now(timezone.utc).isoformat()

    if "device" not in moo_config.load_config():
        partial["device"] = {"id": _derive_device_id(), "version": "0.1.0"}

    merged = moo_config.update_config(partial)

    if moo_config.is_configured():
        try:
            service_control.start_assistant()
        except Exception:
            pass

    return moo_config.redact(merged)


def _derive_device_id() -> str:
    try:
        with open("/sys/class/net/wlan0/address", "r", encoding="utf-8") as f:
            mac = f.read().strip().replace(":", "")
        return f"moo-{mac[-4:]}"
    except OSError:
        return "moo-unknown"
