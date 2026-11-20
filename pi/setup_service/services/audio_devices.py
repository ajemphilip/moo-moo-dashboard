"""Probe USB audio devices via sounddevice/portaudio."""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class AudioDeviceInfo:
    index: int
    name: str
    max_input_channels: int
    max_output_channels: int


def list_devices() -> list[AudioDeviceInfo]:
    try:
        import sounddevice as sd
    except (ImportError, OSError):
        return []
    out: list[AudioDeviceInfo] = []
    for idx, dev in enumerate(sd.query_devices()):
        out.append(AudioDeviceInfo(
            index=idx,
            name=str(dev.get("name", f"device-{idx}")),
            max_input_channels=int(dev.get("max_input_channels", 0)),
            max_output_channels=int(dev.get("max_output_channels", 0)),
        ))
    return out


def first_usb_input(devices: list[AudioDeviceInfo]) -> int | None:
    for d in devices:
        if d.max_input_channels > 0 and "USB" in d.name.upper():
            return d.index
    for d in devices:
        if d.max_input_channels > 0:
            return d.index
    return None


def first_usb_output(devices: list[AudioDeviceInfo]) -> int | None:
    for d in devices:
        if d.max_output_channels > 0 and "USB" in d.name.upper():
            return d.index
    for d in devices:
        if d.max_output_channels > 0:
            return d.index
    return None
