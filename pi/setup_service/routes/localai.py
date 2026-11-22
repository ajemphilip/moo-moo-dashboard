"""Moo Local, handed out by the Moo itself.

GET /local-ai/download/mac       Install Moo Local.command, in a zip (keeps it runnable)
GET /local-ai/download/windows   Install Moo Local.cmd
GET /local-ai/download/linux     Install Moo Local.sh
GET /local-ai/install.sh         the macOS and Linux installer, for the Terminal line
GET /local-ai/install.ps1        the Windows installer, which the .cmd fetches
GET /local-ai/files/{name}       the files the installers fetch

The website's Set up a local machine page offers the download for the
visitor's computer; a parent double-clicks it and the computer then appears
on the AI engine screen. The Terminal line still works on a Mac or Linux:
    curl -fsSL http://moo.local:8000/local-ai/install.sh | bash
Each installer has this Moo's address filled in. The files are a copy of
the `local_ai` folder that `build.sh` places next to the setup app.
"""

from __future__ import annotations

import io
import ipaddress
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response

router = APIRouter(prefix="/local-ai")

_DIR = Path(__file__).resolve().parents[2] / "local_ai"
_FILES = {
    "moo_local.py", "mlx_voice_worker.py", "kitten2_worker.py",
    "requirements-base.txt", "requirements.txt", "requirements-kitten2.txt",
}
_NAME = "Install Moo Local"


def _moo_url(request: Request) -> str:
    """This Moo's address as the computer can reach it. The installers fetch
    over plain http, which the setup app serves on 8000. A name such as
    moo.local is swapped for the address the computer actually used, since
    some computers cannot look up .local names."""
    host = (request.headers.get("host") or "moo.local:8000").rsplit(":", 1)[0].strip("[]")
    server = request.scope.get("server") or (None, None)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        try:
            ip = ipaddress.ip_address(server[0] or "")
            if ip.version == 4 and not ip.is_loopback:
                host = str(ip)
        except ValueError:
            pass
    return f"http://{host}:8000"


def _filled(name: str, request: Request) -> str:
    path = _DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Moo Local is not included in this build")
    return path.read_text().replace("__MOO_URL__", _moo_url(request))


def _attachment(body: bytes, filename: str, media_type: str) -> Response:
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@router.get("/install.sh", response_class=PlainTextResponse)
def install_script(request: Request) -> str:
    return _filled("install.sh", request)


@router.get("/install.ps1", response_class=PlainTextResponse)
def install_powershell(request: Request) -> str:
    return _filled("install.ps1", request)


@router.get("/download/{system}")
def download(system: str, request: Request) -> Response:
    if system == "mac":
        # A .command downloaded on its own loses its "may run" bit; inside a
        # zip it keeps it, and Safari opens the zip by itself.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            info = zipfile.ZipInfo(f"{_NAME}.command", date_time=(2026, 1, 1, 0, 0, 0))
            info.create_system = 3  # unix, so the mode below is read
            info.external_attr = (0o100755 << 16)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, _filled("install.sh", request))
        return _attachment(buf.getvalue(), f"{_NAME}.zip", "application/zip")
    if system == "windows":
        text = _filled("install.cmd", request).replace("\r\n", "\n").replace("\n", "\r\n")
        return _attachment(text.encode(), f"{_NAME}.cmd", "application/octet-stream")
    if system == "linux":
        return _attachment(_filled("install.sh", request).encode(), f"{_NAME}.sh", "application/x-sh")
    raise HTTPException(status_code=404, detail="Choose mac, windows or linux")


@router.get("/files/{name}")
def install_file(name: str) -> FileResponse:
    if name not in _FILES or not (_DIR / name).exists():
        raise HTTPException(status_code=404, detail="No such file")
    return FileResponse(_DIR / name, media_type="text/plain")
