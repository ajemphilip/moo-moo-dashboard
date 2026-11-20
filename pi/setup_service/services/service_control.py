"""systemctl wrappers for the moo-assistant service."""

from __future__ import annotations

import subprocess

ASSISTANT_UNIT = "moo-assistant.service"


def start_assistant() -> None:
    subprocess.run(["sudo", "/usr/bin/systemctl", "start", ASSISTANT_UNIT],
                   check=False, capture_output=True, text=True, timeout=10)


def stop_assistant() -> None:
    subprocess.run(["sudo", "/usr/bin/systemctl", "stop", ASSISTANT_UNIT],
                   check=False, capture_output=True, text=True, timeout=10)


def restart_assistant() -> None:
    subprocess.run(["sudo", "/usr/bin/systemctl", "restart", ASSISTANT_UNIT],
                   check=False, capture_output=True, text=True, timeout=10)


def assistant_status() -> str:
    result = subprocess.run(
        ["/usr/bin/systemctl", "is-active", ASSISTANT_UNIT],
        capture_output=True, text=True, timeout=5, check=False,
    )
    state = result.stdout.strip()
    if state == "active":
        return "running"
    if state == "inactive":
        return "stopped"
    if state == "failed":
        return "failed"
    return state or "unknown"


def is_assistant_running() -> bool:
    return assistant_status() == "running"


def journal_lines(unit: str, lines: int = 200) -> list[str]:
    lines = max(1, min(lines, 500))
    result = subprocess.run(
        ["sudo", "/usr/bin/journalctl", "-u", unit, "-n", str(lines),
         "--no-pager", "--output=short-iso"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    return result.stdout.splitlines()
