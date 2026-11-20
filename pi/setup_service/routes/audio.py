"""Audio device endpoints — list devices, speaker test, microphone test.

Used by the wizard's audio-setup step so the maker can confirm the USB
soundcard's speaker and microphone before finishing setup.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from ..models import (
    AudioDevice,
    AudioDevicesResponse,
    MicTestRequest,
    MicTestResponse,
    SimpleStatus,
    SpeakerTestRequest,
)
from ..services import audio_devices, audio_test

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/audio")


@router.get("/devices", response_model=AudioDevicesResponse)
def list_devices() -> AudioDevicesResponse:
    devs = audio_devices.list_devices()
    return AudioDevicesResponse(
        devices=[
            AudioDevice(
                index=d.index,
                name=d.name,
                max_input_channels=d.max_input_channels,
                max_output_channels=d.max_output_channels,
            )
            for d in devs
        ],
        suggested_input=audio_devices.first_usb_input(devs),
        suggested_output=audio_devices.first_usb_output(devs),
    )


@router.post("/test/speaker", response_model=SimpleStatus)
def test_speaker(req: SpeakerTestRequest) -> SimpleStatus:
    try:
        audio_test.play_tone(req.output_index)
    except Exception as exc:
        logger.exception("Speaker test failed")
        raise HTTPException(
            status_code=500, detail=f"Speaker test failed: {exc}"
        ) from exc
    return SimpleStatus(status="played")


@router.post("/test/mic", response_model=MicTestResponse)
def test_mic(req: MicTestRequest) -> MicTestResponse:
    try:
        recording, rate, peak = audio_test.record_and_measure(
            req.input_index, seconds=3.0
        )
        if req.playback:
            try:
                audio_test.play_back(recording, rate, req.output_index)
            except Exception:
                logger.warning("mic-test playback failed", exc_info=True)
    except Exception as exc:
        logger.exception("Mic test failed")
        raise HTTPException(status_code=500, detail=f"Mic test failed: {exc}") from exc
    return MicTestResponse(
        peak_level=round(peak, 4),
        heard=peak >= audio_test.HEARD_THRESHOLD,
    )
