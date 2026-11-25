"""Using the Moo without internet, and the answers to the systems' internet checks.

GET  /network            — {offline, wifi_connected, ready, missing}
POST /network/offline    — {offline: bool}

Offline means the Moo stays on its own wifi (comitup's hotspot) and a computer
joined to that wifi does the AI with Moo Local. It can only be chosen when the
AI engine is that computer and Moo Local has a story, listening and voice model
installed, since nothing else can answer without internet.

While offline, port 80 on the hotspot comes to this service (see moo-offline),
and the routes at the bottom give macOS, iOS, Android and Windows the page they
expect when they check for internet. Then a Mac stops asking to join another
wifi network. When not offline these routes are never reached on port 80.
"""

from __future__ import annotations

import logging
import subprocess

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

import config as moo_config

from ..services import wifi_manager

logger = logging.getLogger(__name__)
router = APIRouter()

OFFLINE_HELPER = "/usr/local/sbin/moo-offline"
KINDS = {"story": "a story model", "listen": "a listening model", "voice": "a voice"}


def apply_redirect(offline: bool) -> bool:
    """Send hotspot port 80 to this service, or give it back. False if it failed."""
    try:
        subprocess.run(
            ["sudo", OFFLINE_HELPER, "on" if offline else "off"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        return True
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as exc:
        logger.warning("moo-offline %s failed: %s", "on" if offline else "off", exc)
        return False


def restore_on_start() -> None:
    """At service start, put the port 80 redirect back if offline was chosen."""
    try:
        if moo_config.is_offline():
            apply_redirect(True)
    except Exception:
        logger.exception("Could not restore offline mode")


def _missing() -> list[str]:
    """What still has to be done before the Moo can work without internet."""
    from assistant.ai_client import ai_mode, local_url

    cfg = moo_config.load_config()
    if ai_mode(cfg) != "local" or not local_url(cfg):
        return ["Choose a computer with Moo Local on the AI engine screen."]
    from .localai import _relay

    try:
        listing = _relay("/moo/models", timeout=6)
    except HTTPException as exc:
        return [f"{exc.detail} Open Moo Local on that computer and join it to the Moo's wifi."]
    installed = {m.get("kind") for m in listing.get("models", []) if m.get("installed")}
    return [f"Install {label} under Models on the AI engine screen." for kind, label in KINDS.items() if kind not in installed]


@router.get("/network")
def network_state() -> dict:
    missing = _missing()
    return {
        "offline": moo_config.is_offline(),
        "wifi_connected": wifi_manager.is_connected(),
        "ready": not missing,
        "missing": missing,
    }


@router.post("/network/offline")
def set_offline(body: dict) -> dict:
    offline = bool(body.get("offline"))
    if offline:
        missing = _missing()
        if missing:
            raise HTTPException(status_code=409, detail=" ".join(missing))
    moo_config.update_config({"network": {"offline": offline}})
    redirected = apply_redirect(offline)
    return {"offline": offline, "redirected": redirected}


# --- the internet checks of each system ---------------------------------------
# Apple: captive.apple.com/hotspot-detect.html and www.apple.com/library/test/success.html

_APPLE_SUCCESS = "<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>"


@router.get("/hotspot-detect.html", response_class=HTMLResponse, include_in_schema=False)
@router.get("/library/test/success.html", response_class=HTMLResponse, include_in_schema=False)
def apple_check() -> str:
    return _APPLE_SUCCESS


@router.get("/generate_204", include_in_schema=False)
@router.get("/gen_204", include_in_schema=False)
def android_check() -> Response:
    return Response(status_code=204)


@router.get("/connecttest.txt", response_class=PlainTextResponse, include_in_schema=False)
def windows_check() -> str:
    return "Microsoft Connect Test"


@router.get("/ncsi.txt", response_class=PlainTextResponse, include_in_schema=False)
def windows_old_check() -> str:
    return "Microsoft NCSI"
