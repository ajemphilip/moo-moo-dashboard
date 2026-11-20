"""Expose the local Moo HTTP API through a Bluetooth LE GATT bridge.

The browser sends framed JSON requests to REQUEST_UUID. Responses are framed
JSON notifications on RESPONSE_UUID. Keeping the bridge HTTP-shaped lets the
same Next.js pages use either transport without duplicating setup logic.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import signal
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from bless import (
    BlessGATTCharacteristic,
    BlessServer,
    GATTAttributePermissions,
    GATTCharacteristicProperties,
)

from .pairing_agent import BlueZPairingAgent
from .protocol import MessageAssembler, ProtocolError, encode_frames

logger = logging.getLogger(__name__)

SERVICE_UUID = "6d6f6f00-7c5a-4f1b-9e3d-2a6c8b0d1000"
REQUEST_UUID = "6d6f6f00-7c5a-4f1b-9e3d-2a6c8b0d1001"
RESPONSE_UUID = "6d6f6f00-7c5a-4f1b-9e3d-2a6c8b0d1002"
LOCAL_API_BASE = os.environ.get("MOO_BLE_API_BASE", "http://127.0.0.1:8000")
PIN_PATH = Path(os.environ.get("MOO_BLE_PIN_PATH", "/etc/moo/ble-pin"))
HTTP_TIMEOUT_SECONDS = 40
NOTIFICATION_GAP_SECONDS = 0.01

ALLOWED_ENDPOINTS: set[tuple[str, str]] = {
    ("GET", "/status"),
    ("GET", "/config"),
    ("GET", "/logs"),
    ("POST", "/config"),
    ("GET", "/wifi/scan"),
    ("POST", "/wifi/connect"),
    ("GET", "/audio/devices"),
    ("POST", "/audio/test/speaker"),
    ("POST", "/audio/test/mic"),
    ("POST", "/restart"),
    ("POST", "/reset"),
    ("POST", "/braille/test"),
    ("POST", "/braille/calibrate/start"),
    ("POST", "/braille/calibrate/pulse"),
    ("GET", "/braille/calibrate/button"),
    ("POST", "/braille/calibrate/save"),
    ("POST", "/braille/calibrate/stop"),
    ("POST", "/braille/buttons/start"),
    ("GET", "/braille/buttons/state"),
    ("POST", "/braille/buttons/stop"),
    ("POST", "/letters/start"),
    ("POST", "/letters/show"),
    ("GET", "/letters/state"),
    ("POST", "/letters/stop"),
    ("POST", "/story/start"),
    ("POST", "/story/continue"),
    ("GET", "/story/state"),
    ("POST", "/story/stop"),
}


class MooBluetoothBridge:
    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.assembler = MessageAssembler()
        self.server = BlessServer(name=_device_name(), loop=self.loop)
        self.pairing_agent = BlueZPairingAgent()
        self.response_lock = asyncio.Lock()
        self.pin = _load_optional_pin()

    async def start(self) -> None:
        await self.pairing_agent.start()
        await self.server.add_new_service(SERVICE_UUID)
        encrypted_write = (
            GATTAttributePermissions.writeable
            | GATTAttributePermissions.write_encryption_required
        )
        await self.server.add_new_characteristic(
            SERVICE_UUID,
            REQUEST_UUID,
            GATTCharacteristicProperties.write,
            None,
            encrypted_write,
        )
        await self.server.add_new_characteristic(
            SERVICE_UUID,
            RESPONSE_UUID,
            GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify,
            bytearray(),
            GATTAttributePermissions.readable,
        )
        self.server.read_request_func = lambda characteristic: characteristic.value
        self.server.write_request_func = self._on_write
        await self.server.start()
        logger.info("Advertising %s with Moo BLE service %s", _device_name(), SERVICE_UUID)
        if self.pin:
            logger.info("Application setup-code protection is enabled")
        else:
            logger.warning("No %s file; relying on Bluetooth link encryption only", PIN_PATH)

    async def stop(self) -> None:
        try:
            await self.server.stop()
        finally:
            await self.pairing_agent.stop()

    def _on_write(
        self,
        characteristic: BlessGATTCharacteristic,
        value: Any,
        **_kwargs: Any,
    ) -> None:
        if characteristic.uuid.lower() != REQUEST_UUID:
            return
        frame = bytes(value)
        self.loop.call_soon_threadsafe(self._accept_frame, frame)

    def _accept_frame(self, frame: bytes) -> None:
        try:
            complete = self.assembler.accept(frame)
        except ProtocolError as exc:
            logger.warning("Discarding malformed BLE frame: %s", exc)
            return
        if complete is not None:
            request_id, payload = complete
            self.loop.create_task(self._handle_request(request_id, payload))

    async def _handle_request(self, request_id: int, payload: bytes) -> None:
        try:
            message = json.loads(payload.decode("utf-8"))
            if not isinstance(message, dict):
                raise ValueError("request must be a JSON object")
            method = str(message.get("method", "GET")).upper()
            path = str(message.get("path", ""))
            path_without_query = path.split("?", 1)[0]
            if (method, path_without_query) not in ALLOWED_ENDPOINTS:
                response = {"ok": False, "status": 404, "detail": "Unsupported Moo Bluetooth command"}
            elif self.pin and not hmac.compare_digest(str(message.get("pin", "")), self.pin):
                response = {"ok": False, "status": 401, "detail": "Incorrect Moo setup code"}
            else:
                response = await asyncio.to_thread(
                    _proxy_http, method, path, message.get("body")
                )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            response = {"ok": False, "status": 400, "detail": f"Invalid Bluetooth request: {exc}"}
        except Exception:
            logger.exception("Unexpected BLE bridge failure")
            response = {"ok": False, "status": 500, "detail": "Bluetooth bridge error"}
        await self._send_response(request_id, response)

    async def _send_response(self, request_id: int, response: dict[str, Any]) -> None:
        try:
            payload = json.dumps(response, separators=(",", ":")).encode("utf-8")
            frames = encode_frames(request_id, payload)
        except ProtocolError:
            fallback = json.dumps({
                "ok": False,
                "status": 413,
                "detail": "Moo response is too large for Bluetooth; use wifi for this action",
            }, separators=(",", ":")).encode("utf-8")
            frames = encode_frames(request_id, fallback)

        async with self.response_lock:
            characteristic = self.server.get_characteristic(RESPONSE_UUID)
            if characteristic is None:
                return
            for frame in frames:
                characteristic.value = bytearray(frame)
                self.server.update_value(SERVICE_UUID, RESPONSE_UUID)
                await asyncio.sleep(NOTIFICATION_GAP_SECONDS)


def _proxy_http(method: str, path: str, body: Any) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{LOCAL_API_BASE}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "X-Moo-Setup": "1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as result:
            raw = result.read()
            parsed = json.loads(raw) if raw else None
            return {"ok": True, "status": result.status, "data": parsed}
    except urllib.error.HTTPError as exc:
        try:
            parsed_error = json.loads(exc.read())
            detail = str(parsed_error.get("detail", exc.reason))
        except (json.JSONDecodeError, AttributeError):
            detail = str(exc.reason)
        return {"ok": False, "status": exc.code, "detail": detail}
    except urllib.error.URLError:
        return {"ok": False, "status": 503, "detail": "Moo setup service is unavailable"}


def _device_name() -> str:
    try:
        mac = Path("/sys/class/net/wlan0/address").read_text(encoding="utf-8").strip()
        return f"Moo-{mac.replace(':', '')[-4:].upper()}"
    except OSError:
        return "Moo-Setup"


def _load_optional_pin() -> str | None:
    try:
        pin = PIN_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return pin if len(pin) == 6 and pin.isdigit() else None


async def run() -> None:
    bridge = MooBluetoothBridge()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    await bridge.start()
    try:
        await stopped.wait()
    finally:
        await bridge.stop()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())
