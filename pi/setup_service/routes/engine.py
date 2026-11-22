"""Lend a device to Moo: a phone or laptop browser does the heavy lifting.

A device opens the Lend page and says hello. From then on it asks the Moo
for work (long polling), does it in the browser on its own graphics chip,
and sends the result back. The Moo never needs to reach the device; the
device always calls the Moo, so any browser on the home network can help.

Jobs:
  chat        {messages, max_tokens, response_format}  -> {content}
  transcribe  {wav_b64}                                 -> {text}
  speak       {text}                                    -> {wav_b64}
  moderate    {text}                                    -> {flagged}

POST /engine/hello    — {name, device} -> {engine_id}
GET  /engine/next     — ?engine_id=…  waits up to 25 s for a job
POST /engine/result   — {job_id, ok, result?, error?}
GET  /engine/status   — devices lent right now and jobs waiting
POST /engine/submit   — {kind, payload, timeout} waits for the result (used by the Moo itself)
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
import uuid
from collections import deque

from fastapi import APIRouter, HTTPException

import config as moo_config

from ..models import EngineHello, EngineResult, EngineSubmit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/engine")

ENGINE_STALE_SECONDS = 40.0   # a device that has not asked for work this long is gone
POLL_SECONDS = 25.0

_cond = threading.Condition()
_engines: dict[str, dict] = {}
_queue: deque[dict] = deque()
_results: dict[str, dict] = {}
_ids = itertools.count(1)


def _alive() -> list[dict]:
    now = time.monotonic()
    return [e for e in _engines.values() if now - e["last_seen"] < ENGINE_STALE_SECONDS]


# --- the current device -----------------------------------------------------
# Several phones and computers may be lent at once. One of them is the current
# device and gets every job; the others wait until they are chosen. The choice
# is kept by name in the configuration (ai.lent_device), so it outlasts a
# reconnect, which gives a device a new engine_id.

def _current_name() -> str | None:
    try:
        return ((moo_config.load_config().get("ai") or {}).get("lent_device")) or None
    except Exception:
        return None


def _set_current(name: str) -> None:
    moo_config.update_config({"ai": {"lent_device": name}})


def _takes_jobs(eng: dict) -> bool:
    """The current device takes the jobs. If it is not lending right now, any
    lent device may, so a story never waits on a phone that was put away."""
    current = _current_name()
    if not current or eng["name"] == current:
        return True
    return not any(e["name"] == current for e in _alive())


def engine_available() -> bool:
    with _cond:
        return bool(_alive())


def submit(kind: str, payload: dict, timeout: float = 120.0) -> dict:
    """Queue a job for a lent device and wait for its result."""
    job_id = f"j{next(_ids)}"
    with _cond:
        if not _alive():
            raise RuntimeError(
                "No device is lent to Moo. Open the Lend page on a phone or laptop "
                "(https://moo.local:8443/lend) and keep it open."
            )
        _queue.append({"id": job_id, "kind": kind, "payload": payload, "queued": time.monotonic()})
        _cond.notify_all()
        deadline = time.monotonic() + timeout
        while job_id not in _results:
            left = deadline - time.monotonic()
            if left <= 0:
                for j in list(_queue):
                    if j["id"] == job_id:
                        _queue.remove(j)
                raise RuntimeError(f"The lent device did not answer the {kind} job in time")
            _cond.wait(timeout=min(left, 1.0))
        res = _results.pop(job_id)
    if not res.get("ok"):
        raise RuntimeError(f"The lent device could not do the {kind} job: {res.get('error')}")
    return res.get("result") or {}


@router.post("/hello")
def hello(req: EngineHello) -> dict:
    engine_id = uuid.uuid4().hex[:12]
    with _cond:
        # The same device saying hello again (the app was reopened, the page
        # reloaded) replaces its old entry at once. Left in place, the old one
        # counted as lent for 40 seconds and a story's first job went to it and
        # was never answered (21 November 2026).
        for old in [k for k, e in _engines.items() if e["name"] == req.name]:
            del _engines[old]
        _engines[engine_id] = {
            "id": engine_id, "name": req.name, "device": req.device,
            "last_seen": time.monotonic(), "jobs_done": 0,
        }
        _cond.notify_all()
    if not _current_name():
        _set_current(req.name)  # the first device lent becomes the current one
    logger.info("device lent to Moo: %s (%s)", req.name, req.device)
    return {"engine_id": engine_id}


@router.post("/use")
def use(body: dict) -> dict:
    """Make a lent device the current one: it gets the jobs from now on."""
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Which device?")
    _set_current(name)
    with _cond:
        _cond.notify_all()
    logger.info("current lent device: %s", name)
    return {"current": name}


@router.post("/bye")
def bye(body: dict) -> dict:
    """A device stops lending: it leaves the list at once."""
    with _cond:
        _engines.pop(str(body.get("engine_id") or ""), None)
        _cond.notify_all()
    return {"ok": True}


@router.get("/next")
def next_job(engine_id: str) -> dict:
    deadline = time.monotonic() + POLL_SECONDS
    with _cond:
        eng = _engines.get(engine_id)
        if eng is None:
            raise HTTPException(status_code=404, detail="Unknown device, say hello again")
        while True:
            eng["last_seen"] = time.monotonic()
            if _engines.get(engine_id) is not eng:
                raise HTTPException(status_code=404, detail="Replaced by a newer hello")
            if _queue and _takes_jobs(eng):
                job = _queue.popleft()
                eng["busy_since"] = time.monotonic()
                return {"job": {"id": job["id"], "kind": job["kind"], "payload": job["payload"]}}
            left = deadline - time.monotonic()
            if left <= 0:
                return {"job": None}
            _cond.wait(timeout=min(left, 5.0))


@router.post("/result")
def result(req: EngineResult) -> dict:
    with _cond:
        _results[req.job_id] = {"ok": req.ok, "result": req.result, "error": req.error}
        if req.engine_id in _engines:
            _engines[req.engine_id]["jobs_done"] += 1
            _engines[req.engine_id]["last_seen"] = time.monotonic()
        _cond.notify_all()
    return {"ok": True}


@router.post("/report")
def report(body: dict) -> dict:
    """A lending browser says what it is loading, before it loads it. A phone
    browser that runs out of memory closes the page without a word, so the
    last stage reported here is where it died."""
    logger.info("lending device %s: %s", str(body.get("device", "?"))[:120], str(body.get("stage", "?"))[:120])
    return {"ok": True}


@router.get("/status")
def status() -> dict:
    current = _current_name()
    with _cond:
        now = time.monotonic()
        alive = _alive()
        working = next((e["name"] for e in alive if _takes_jobs(e)), None)
        return {
            "devices": [
                {"name": e["name"], "device": e["device"], "jobs_done": e["jobs_done"],
                 "seconds_since_seen": round(now - e["last_seen"], 1),
                 "current": e["name"] == working}
                for e in sorted(alive, key=lambda e: e["name"] != working)
            ],
            "current": current,  # the device chosen, lending or not
            "queued": len(_queue),
        }


@router.post("/submit")
def submit_route(req: EngineSubmit) -> dict:
    try:
        return submit(req.kind, req.payload, req.timeout)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
