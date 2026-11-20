"""Run the setup service on plain http (8000) and on https (8443) at once.

Both serve the same app in one process, so a story or a game started on one
is the same session on the other. https is needed by the "Lend this device"
page: browsers only allow on-device AI (WebGPU) and keeping a phone awake on
secure pages. http stays for the Bluetooth bridge and for old links.

The certificate is made on the device the first time (self-signed, for
moo.local and the device's current address). Each browser warns about it
once; after "continue" the page counts as secure.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import subprocess
from pathlib import Path

import uvicorn

logger = logging.getLogger("moo-setup")

TLS_DIR = Path(os.environ.get("MOO_TLS_DIR", "/etc/moo/tls"))
CERT = TLS_DIR / "cert.pem"
KEY = TLS_DIR / "key.pem"
HTTP_PORT = int(os.environ.get("MOO_HTTP_PORT", "8000"))
HTTPS_PORT = int(os.environ.get("MOO_HTTPS_PORT", "8443"))


def _lan_ip() -> str | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def ensure_certificate() -> bool:
    """Make a self-signed certificate for moo.local if there is none."""
    if CERT.exists() and KEY.exists():
        return True
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    host = socket.gethostname() or "moo"
    san = f"DNS:{host}.local,DNS:{host},DNS:localhost,IP:127.0.0.1"
    ip = _lan_ip()
    if ip:
        san += f",IP:{ip}"
    try:
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(KEY), "-out", str(CERT), "-days", "3650",
                "-subj", f"/CN={host}.local/O=Moo",
                "-addext", f"subjectAltName={san}",
            ],
            check=True, capture_output=True, timeout=120,
        )
        os.chmod(KEY, 0o600)
        logger.info("made a certificate for %s", san)
        return True
    except Exception:
        logger.exception("could not make a certificate; https stays off")
        return False


async def _main() -> None:
    from setup_service.main import app

    servers = [
        uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=HTTP_PORT, log_level="info"))
    ]
    if ensure_certificate():
        servers.append(
            uvicorn.Server(
                uvicorn.Config(
                    app, host="0.0.0.0", port=HTTPS_PORT, log_level="info",
                    ssl_certfile=str(CERT), ssl_keyfile=str(KEY),
                )
            )
        )
    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(_main())
