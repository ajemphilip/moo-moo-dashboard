"""Small framed-message protocol shared conceptually with the Web Bluetooth client."""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field

HEADER = struct.Struct("<HHH")  # request id, zero-based chunk index, total chunks
FRAME_BYTES = 20
PAYLOAD_BYTES = FRAME_BYTES - HEADER.size
MAX_MESSAGE_BYTES = 32_768
MAX_CHUNKS = (MAX_MESSAGE_BYTES + PAYLOAD_BYTES - 1) // PAYLOAD_BYTES
ASSEMBLY_TTL_SECONDS = 60.0


class ProtocolError(ValueError):
    """A malformed or oversized BLE frame was received."""


@dataclass
class _Assembly:
    total: int
    created_at: float = field(default_factory=time.monotonic)
    chunks: dict[int, bytes] = field(default_factory=dict)


class MessageAssembler:
    def __init__(self) -> None:
        self._assemblies: dict[int, _Assembly] = {}

    def accept(self, frame: bytes) -> tuple[int, bytes] | None:
        self._expire_old()
        if len(frame) < HEADER.size or len(frame) > FRAME_BYTES:
            raise ProtocolError("invalid BLE frame length")
        request_id, index, total = HEADER.unpack_from(frame)
        if request_id == 0:
            raise ProtocolError("request id zero is reserved")
        if total == 0 or total > MAX_CHUNKS or index >= total:
            raise ProtocolError("invalid BLE chunk numbering")

        assembly = self._assemblies.get(request_id)
        if assembly is None:
            assembly = _Assembly(total=total)
            self._assemblies[request_id] = assembly
        elif assembly.total != total:
            del self._assemblies[request_id]
            raise ProtocolError("BLE chunk count changed mid-message")

        assembly.chunks[index] = frame[HEADER.size:]
        if len(assembly.chunks) != total:
            return None

        payload = b"".join(assembly.chunks[i] for i in range(total))
        del self._assemblies[request_id]
        if len(payload) > MAX_MESSAGE_BYTES:
            raise ProtocolError("BLE message is too large")
        return request_id, payload

    def _expire_old(self) -> None:
        deadline = time.monotonic() - ASSEMBLY_TTL_SECONDS
        for request_id in [
            key for key, value in self._assemblies.items() if value.created_at < deadline
        ]:
            del self._assemblies[request_id]


def encode_frames(request_id: int, payload: bytes) -> list[bytes]:
    if request_id <= 0 or request_id > 0xFFFF:
        raise ProtocolError("invalid request id")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ProtocolError("BLE message is too large")
    total = max(1, (len(payload) + PAYLOAD_BYTES - 1) // PAYLOAD_BYTES)
    return [
        HEADER.pack(request_id, index, total)
        + payload[index * PAYLOAD_BYTES:(index + 1) * PAYLOAD_BYTES]
        for index in range(total)
    ]
