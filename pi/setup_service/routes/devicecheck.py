"""GET /device-check — a small page that tells whether this browser can lend
itself to Moo (secure page, WebGPU, memory). The result is logged on the Pi
and listed by GET /device-check/results, so the check can be read remotely.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)
router = APIRouter()
_results: list[dict] = []

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Moo device check</title>
<style>body{font-family:system-ui;max-width:32rem;margin:2rem auto;padding:0 1rem;background:#fff8ec;color:#2b2118}
li{margin:.4rem 0}.ok{color:#15803d}.no{color:#b91c1c}</style></head><body>
<h1>Can this device help Moo?</h1><ul id="r"><li>Checking…</li></ul>
<script>
(async () => {
  const out = {};
  out.secure = window.isSecureContext;
  out.webgpu = !!navigator.gpu;
  out.adapter = false;
  try { if (navigator.gpu) { const a = await navigator.gpu.requestAdapter(); out.adapter = !!a;
        if (a && a.info) out.gpu = (a.info.vendor||"") + " " + (a.info.architecture||""); } } catch (e) { out.adapter_error = String(e); }
  out.memory_gb = navigator.deviceMemory || null;
  out.cores = navigator.hardwareConcurrency || null;
  out.wakelock = "wakeLock" in navigator;
  out.agent = navigator.userAgent;
  const rows = [
    ["Secure page", out.secure], ["WebGPU in this browser", out.webgpu],
    ["Graphics chip available", out.adapter], ["Keep screen awake", out.wakelock]];
  document.getElementById("r").innerHTML = rows.map(([k,v]) =>
    `<li class="${v?"ok":"no"}">${v?"✓":"✗"} ${k}</li>`).join("") +
    `<li>Memory: ${out.memory_gb ?? "?"} GB, cores: ${out.cores ?? "?"}</li>` +
    `<li><b>${out.secure && out.adapter ? "This device can run Moo's AI." : "This device cannot run Moo's AI here."}</b></li>`;
  fetch("/device-check/report", {method:"POST", headers:{"Content-Type":"application/json","X-Moo-Setup":"1"}, body: JSON.stringify(out)});
})();
</script></body></html>"""


@router.get("/device-check", response_class=HTMLResponse)
def page() -> str:
    return _PAGE


@router.post("/device-check/report")
async def report(request: Request) -> dict:
    data = await request.json()
    data["at"] = time.strftime("%H:%M:%S")
    data["via"] = request.url.scheme
    _results.insert(0, data)
    del _results[20:]
    logger.info("device check: %s", data)
    return {"ok": True}


@router.get("/device-check/results")
def results() -> list[dict]:
    return _results
