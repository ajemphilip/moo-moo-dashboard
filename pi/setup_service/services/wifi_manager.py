"""Wifi state via `comitup-cli` and `iw dev wlan0 scan`.

We shell out to `comitup-cli` for connect/delete because it's the documented
public interface (the dbus path varies between versions). `iw` handles
scanning since comitup disables NetworkManager's wifi management.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

WIFI_INTERFACE = "wlan0"
SCAN_CACHE_TTL_SECONDS = 10
COMITUP_CLI = "/usr/sbin/comitup-cli"

_scan_cache: tuple[float, list["Network"]] | None = None


@dataclass
class Network:
    ssid: str
    signal: int
    secured: bool


def scan_networks() -> list[Network]:
    global _scan_cache
    now = time.monotonic()
    if _scan_cache and (now - _scan_cache[0]) < SCAN_CACHE_TTL_SECONDS:
        return _scan_cache[1]

    try:
        result = subprocess.run(
            ["sudo", "/usr/sbin/iw", "dev", WIFI_INTERFACE, "scan"],
            capture_output=True, text=True, timeout=15, check=True,
        )
    except subprocess.CalledProcessError as exc:
        logger.warning("iw scan failed: %s", exc.stderr)
        return _scan_cache[1] if _scan_cache else []
    except FileNotFoundError:
        return []

    networks = _parse_iw_scan(result.stdout)
    _scan_cache = (now, networks)
    return networks


def connect_to_network(ssid: str, password: str) -> None:
    """Trigger comitup's HOTSPOT → CONNECTED transition with the given creds.

    `comitup-cli m <ssid> <password>` runs `do_connect` once and exits. comitup
    then drops AP mode and joins the requested network. The Pi appears on the
    home subnet within ~20s.
    """
    args = ["sudo", COMITUP_CLI, "m", ssid, password]
    subprocess.run(args, check=True, capture_output=True, text=True, timeout=15)


def reset_to_hotspot() -> None:
    subprocess.run(
        ["sudo", COMITUP_CLI, "d"],
        check=False, capture_output=True, text=True, timeout=10,
    )


def is_connected() -> bool:
    return current_ssid() is not None


def current_ssid() -> str | None:
    try:
        result = subprocess.run(
            ["/usr/sbin/iw", "dev", WIFI_INTERFACE, "link"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except FileNotFoundError:
        return None
    if "Not connected" in result.stdout:
        return None
    match = re.search(r"SSID:\s*(.+)", result.stdout)
    return _unescape_ssid(match.group(1).strip()) if match else None


def _unescape_ssid(raw: str) -> str:
    """iw prints each byte of a name that is not plain ASCII as \\xNN, so an
    iPhone's "Philip’s iPhone" arrives as "Philip\\xe2\\x80\\x99s iPhone".
    Turn the escapes back into bytes and read them as UTF-8."""
    out = bytearray()
    i = 0
    while i < len(raw):
        if raw.startswith("\\x", i) and i + 4 <= len(raw):
            try:
                out.append(int(raw[i + 2:i + 4], 16))
                i += 4
                continue
            except ValueError:
                pass
        out.extend(raw[i].encode("utf-8"))
        i += 1
    return out.decode("utf-8", errors="replace")


def _parse_iw_scan(output: str) -> list[Network]:
    networks: dict[str, Network] = {}
    current: dict[str, object] = {}

    def flush() -> None:
        ssid = current.get("ssid")
        if not isinstance(ssid, str) or not ssid:
            return
        signal_pct = _dbm_to_pct(current.get("signal_dbm"))
        secured = bool(current.get("secured"))
        existing = networks.get(ssid)
        if existing is None or signal_pct > existing.signal:
            networks[ssid] = Network(ssid=ssid, signal=signal_pct, secured=secured)

    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("BSS "):
            flush()
            current = {"secured": False}
            continue
        if stripped.startswith("signal:"):
            try:
                current["signal_dbm"] = float(stripped.split()[1])
            except (IndexError, ValueError):
                pass
        elif stripped.startswith("SSID:"):
            current["ssid"] = _unescape_ssid(line.split("SSID:", 1)[1].lstrip(" \t").rstrip("\n"))
        elif "Privacy" in stripped or "RSN:" in stripped or "WPA:" in stripped:
            current["secured"] = True
    flush()
    return sorted(networks.values(), key=lambda n: n.signal, reverse=True)


def _dbm_to_pct(dbm: object) -> int:
    if not isinstance(dbm, (int, float)):
        return 0
    if dbm >= -50:
        return 100
    if dbm <= -100:
        return 0
    return int(2 * (dbm + 100))
