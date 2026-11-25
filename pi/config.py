"""Atomic JSON config at /etc/moo/config.json.

Single source of truth for both the setup service and the assistant. The setup
service writes here when the parent completes the wizard; the assistant reads
here on startup.

Atomicity matters because a power-loss mid-write would otherwise corrupt the
file and brick subsequent boots until factory reset.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(os.environ.get("MOO_CONFIG_PATH", "/etc/moo/config.json"))
CONFIG_OWNER = os.environ.get("MOO_CONFIG_OWNER", "philipn1")
CONFIG_MODE = 0o600

_lock = threading.Lock()


def load_config() -> dict[str, Any]:
    """Return the config dict, or an empty dict if the file doesn't exist."""
    with _lock:
        if not CONFIG_PATH.exists():
            return {}
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)


def save_config(data: dict[str, Any]) -> None:
    """Write the config atomically: temp file in same dir → fsync → replace."""
    with _lock:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=".config.", suffix=".tmp", dir=str(CONFIG_PATH.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp_path, CONFIG_MODE)
            try:
                shutil.chown(tmp_path, user=CONFIG_OWNER, group=CONFIG_OWNER)
            except (LookupError, PermissionError):
                pass
            os.replace(tmp_path, CONFIG_PATH)
            dir_fd = os.open(str(CONFIG_PATH.parent), os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise


def update_config(partial: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge partial into the existing config and persist."""
    current = load_config()
    merged = _deep_merge(current, partial)
    save_config(merged)
    return merged


def is_configured() -> bool:
    """True iff the chosen AI is set up (an OpenAI key, or a local computer)
    and the Moo has a wifi SSID, or was told to work without internet."""
    from assistant.ai_client import ai_ready

    cfg = load_config()
    return ai_ready(cfg) and (bool(cfg.get("wifi", {}).get("ssid")) or is_offline(cfg))


def is_offline(cfg: dict[str, Any] | None = None) -> bool:
    """True when the parent chose to use the Moo on its own wifi, without
    internet, with a computer's Moo Local doing the AI."""
    cfg = load_config() if cfg is None else cfg
    return bool((cfg.get("network") or {}).get("offline"))


def reset_config() -> None:
    """Delete the config file. Used by POST /reset."""
    with _lock:
        if CONFIG_PATH.exists():
            CONFIG_PATH.unlink()


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def redact(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the config with sensitive fields masked."""
    out = json.loads(json.dumps(cfg))
    key = out.get("openai_api_key")
    if isinstance(key, str) and len(key) > 8:
        out["openai_api_key"] = f"{key[:4]}...{key[-4:]}"
    elif key:
        out["openai_api_key"] = "***"
    return out
