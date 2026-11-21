"""Moo Local, handed out by the Moo itself.

GET /local-ai/install.sh     — the installer, with this Moo's address filled in
GET /local-ai/files/{name}   — the files the installer fetches

A computer on the home network runs one line in its Terminal,
    curl -fsSL http://moo.local:8000/local-ai/install.sh | bash
and then appears on the AI engine screen. The files are a copy of the
`local_ai` folder that `build.sh` places next to the setup app.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

router = APIRouter(prefix="/local-ai")

_DIR = Path(__file__).resolve().parents[2] / "local_ai"
_FILES = {
    "moo_local.py", "mlx_voice_worker.py", "kitten2_worker.py",
    "requirements-base.txt", "requirements.txt", "requirements-kitten2.txt",
}


@router.get("/install.sh", response_class=PlainTextResponse)
def install_script(request: Request) -> str:
    path = _DIR / "install.sh"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Moo Local is not included in this build")
    host = (request.headers.get("host") or "moo.local:8000").split(":")[0]
    # The installer fetches over plain http, which the setup app serves on 8000.
    return path.read_text().replace("__MOO_URL__", f"http://{host}:8000")


@router.get("/files/{name}")
def install_file(name: str) -> FileResponse:
    if name not in _FILES or not (_DIR / name).exists():
        raise HTTPException(status_code=404, detail="No such file")
    return FileResponse(_DIR / name, media_type="text/plain")
