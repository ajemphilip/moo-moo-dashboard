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
        _engines[engine_id] = {
            "id": engine_id, "name": req.name, "device": req.device,
            "last_seen": time.monotonic(), "jobs_done": 0,
        }
    logger.info("device lent to Moo: %s (%s)", req.name, req.device)
    return {"engine_id": engine_id}


@router.get("/next")
def next_job(engine_id: str) -> dict:
    deadline = time.monotonic() + POLL_SECONDS
    with _cond:
        eng = _engines.get(engine_id)
        if eng is None:
            raise HTTPException(status_code=404, detail="Unknown device, say hello again")
        while True:
            eng["last_seen"] = time.monotonic()
            if _queue:
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


@router.get("/status")
def status() -> dict:
    with _cond:
        now = time.monotonic()
        return {
            "devices": [
                {"name": e["name"], "device": e["device"], "jobs_done": e["jobs_done"],
                 "seconds_since_seen": round(now - e["last_seen"], 1)}
                for e in _alive()
            ],
            "queued": len(_queue),
        }


@router.post("/submit")
def submit_route(req: EngineSubmit) -> dict:
    try:
        return submit(req.kind, req.payload, req.timeout)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
