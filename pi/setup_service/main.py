"""FastAPI entry point for the Moo setup service.

Run via uvicorn — `uvicorn setup_service.main:app --host 0.0.0.0 --port 8000`.
The systemd unit handles this in production. CORS is open (local-network only)
plus a CSRF guard via the X-Moo-Setup header.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .routes import audio as audio_routes
from .routes import braille as braille_routes
from .routes import config as config_routes
from .routes import letters as letters_routes
from .routes import status as status_routes
from .routes import story as story_routes
from .routes import system as system_routes
from .routes import wifi as wifi_routes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="Moo Setup", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Moo-Setup"],
    allow_credentials=False,
)


@app.middleware("http")
async def require_setup_header(request: Request, call_next):
    if request.method not in ("GET", "OPTIONS", "HEAD"):
        if request.headers.get("X-Moo-Setup") != "1":
            return JSONResponse(
                status_code=403,
                content={"detail": "Missing X-Moo-Setup header"},
            )
    return await call_next(request)


app.include_router(status_routes.router)
app.include_router(wifi_routes.router)
app.include_router(config_routes.router)
app.include_router(system_routes.router)
app.include_router(braille_routes.router)
app.include_router(audio_routes.router)
app.include_router(letters_routes.router)
app.include_router(story_routes.router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"service": "moo-setup", "version": "0.1.0"}


# Mount the bundled PWA at / (last, so API routes win for /status etc.)
_DEFAULT_STATIC = Path(__file__).resolve().parent / "static"
_STATIC_DIR = Path(os.environ.get("MOO_STATIC_DIR", str(_DEFAULT_STATIC)))
if _STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="pwa")
else:
    logging.getLogger(__name__).info(
        "No PWA bundle at %s — API-only mode. Run build.sh to populate it.",
        _STATIC_DIR,
    )
