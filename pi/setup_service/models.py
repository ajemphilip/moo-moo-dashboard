"""Pydantic schemas for the setup service request/response bodies."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class StatusResponse(BaseModel):
    configured: bool
    wifi_connected: bool
    wifi_ssid: Optional[str] = None
    assistant_running: bool
    device_id: str
    version: str
    uptime_seconds: int
    # The Moo's address on the home wifi, so a phone that found it over
    # Bluetooth can reach it there for the AI work.
    address: Optional[str] = None


class WifiNetwork(BaseModel):
    ssid: str
    signal: int = Field(ge=0, le=100)
    secured: bool


class WifiScanResponse(BaseModel):
    networks: list[WifiNetwork]


class WifiConnectRequest(BaseModel):
    ssid: str = Field(min_length=1, max_length=32)
    password: str = Field(default="", max_length=63)


class WifiConnectResponse(BaseModel):
    status: Literal["connecting"] = "connecting"


class Preferences(BaseModel):
    tts_voice: Optional[str] = None
    age_range: Optional[Literal["0-3", "3-5", "5-8"]] = None
    volume: Optional[int] = Field(default=None, ge=0, le=100)
    language: Optional[str] = None


class AudioConfig(BaseModel):
    input_index: Optional[int] = None
    output_index: Optional[int] = None


class BrailleGpioPins(BaseModel):
    # 6 motors, each an [AIN1, AIN2] BCM pin pair.
    motor_pins: Optional[list[list[int]]] = None
    # One push button per motor (6 BCM pins).
    button_pins: Optional[list[int]] = None
    # TB6612 STBY enable line (single BCM pin, all boards tied).
    stby_pin: Optional[int] = None


class BrailleConfig(BaseModel):
    gpio: Optional[BrailleGpioPins] = None


class ConfigUpdateRequest(BaseModel):
    """All fields optional — POST /config does a partial merge."""

    openai_api_key: Optional[str] = None
    preferences: Optional[Preferences] = None
    audio: Optional[AudioConfig] = None
    braille: Optional[BrailleConfig] = None


class AudioDevice(BaseModel):
    index: int
    name: str
    max_input_channels: int
    max_output_channels: int


class AudioDevicesResponse(BaseModel):
    devices: list[AudioDevice]
    suggested_input: Optional[int] = None
    suggested_output: Optional[int] = None


class SpeakerTestRequest(BaseModel):
    output_index: Optional[int] = None


class MicTestRequest(BaseModel):
    input_index: Optional[int] = None
    output_index: Optional[int] = None
    playback: bool = True


class MicTestResponse(BaseModel):
    peak_level: float
    heard: bool


class SimpleStatus(BaseModel):
    status: str


class LogsResponse(BaseModel):
    service: str
    lines: list[str]


class BrailleTestRequest(BaseModel):
    """If motor_index is null, all 6 motors are tested in sequence."""

    motor_index: Optional[int] = Field(default=None, ge=0, le=5)


class BrailleTestResponse(BaseModel):
    status: Literal["ok", "dry_run"]
    motors_tested: list[int]
    duration_seconds: float


class CalibrateStartResponse(BaseModel):
    status: str
    dry_run: bool
    motor_pins: list[list[int]]
    button_pins: list[int]


class MotorPulseRequest(BaseModel):
    motor_index: int = Field(ge=0, le=5)
    # "cycle" = raise then lower (motor calibration);
    # "up" = raise and hold; "down" = lower (button calibration).
    direction: Literal["cycle", "up", "down"] = "cycle"


class CalibrateButtonResponse(BaseModel):
    button: Optional[int] = None


class CalibrationSaveRequest(BaseModel):
    # 6 motors in dot-position order, each [IN1, IN2] (already direction-corrected).
    motor_pins: list[list[int]] = Field(min_length=6, max_length=6)
    # 6 button BCM pins in dot-position order.
    button_pins: list[int] = Field(min_length=6, max_length=6)


# --- Lend a device -------------------------------------------------------------


class EngineHello(BaseModel):
    name: str = "A browser"
    device: str = ""


class EngineResult(BaseModel):
    engine_id: str = ""
    job_id: str
    ok: bool
    result: Optional[dict] = None
    error: Optional[str] = None


class EngineSubmit(BaseModel):
    kind: Literal["chat", "transcribe", "speak", "moderate"]
    payload: dict = {}
    timeout: float = 120.0


# --- AI engine ----------------------------------------------------------------


class AiSettings(BaseModel):
    mode: Literal["cloud", "local", "browser"] = "cloud"
    local_url: Optional[str] = None
    local_name: Optional[str] = None
    has_openai_key: bool = False
    ready: bool = False
    # Choices made on the AI engine screen. None means the engine's default.
    tier: Optional[str] = None                 # local: instant | fast | balanced | high | top
    story_model: Optional[str] = None          # local: an Ollama model
    listen_model: Optional[str] = None         # local: a Whisper size
    voice_engine: Optional[str] = None         # local: mac | piper | kokoro | orpheus | chatterbox
    voice: Optional[str] = None                # local and browser: a Kokoro voice
    expressiveness: float = 0.6                # chatterbox, 0.25 calm to 1.0 dramatic
    voice_qualities: dict[str, str] = {}       # local: the quality chosen for each voice model
    browser_quality: str = "auto"              # browser: instant | auto | fast | better
    clarity_db: float = 12.0                   # treble lift for Moo's speaker, 0 to 18 dB
    cloud_story_model: Optional[str] = None    # cloud: gpt-4o-mini | gpt-4o
    cloud_voice_model: Optional[str] = None    # cloud: tts-1 | gpt-4o-mini-tts


class AiUpdateRequest(BaseModel):
    mode: Optional[Literal["cloud", "local", "browser"]] = None
    local_url: Optional[str] = None
    local_name: Optional[str] = None
    tier: Optional[str] = None
    story_model: Optional[str] = None
    listen_model: Optional[str] = None
    voice_engine: Optional[str] = None
    voice: Optional[str] = None
    expressiveness: Optional[float] = None
    voice_qualities: Optional[dict[str, str]] = None
    browser_quality: Optional[str] = None
    clarity_db: Optional[float] = None
    cloud_story_model: Optional[str] = None
    cloud_voice_model: Optional[str] = None


class AiEngine(BaseModel):
    name: str
    host: str
    url: str
    reachable: bool
    ready: bool


class AiDiscoverResponse(BaseModel):
    engines: list[AiEngine] = []


class AiTestResponse(BaseModel):
    mode: str
    line: str
    story_seconds: float
    voice_seconds: float
    voice_bytes: int


# --- Button check -------------------------------------------------------------


class ButtonPress(BaseModel):
    button: int
    seconds_ago: float


class ButtonCheckState(BaseModel):
    active: bool
    dry_run: bool = False
    # Presses counted per button since the check started, in button order 0-5.
    counts: list[int] = [0, 0, 0, 0, 0, 0]
    last: Optional[int] = None
    # Most recent presses, newest first.
    recent: list[ButtonPress] = []


# --- Word games on their own ---------------------------------------------------


class GameStartRequest(BaseModel):
    game: Literal["spell", "name", "build", "mix"] = "mix"


class GameState(BaseModel):
    active: bool
    dry_run: bool = False
    # The game chosen on the phone: spell | name | build | mix
    game: Optional[str] = None
    # idle | telling | thinking | quiz | listening | spelling | done | error
    phase: str = "idle"
    message: Optional[str] = None
    rounds: int = 0
    spelling_word: Optional[str] = None
    game_mode: Optional[str] = None
    build_raised: list[int] = []
    build_target: list[int] = []
    spell_letter: Optional[str] = None
    spell_index: Optional[int] = None
    spell_total: Optional[int] = None
    spell_dots_left: Optional[int] = None
    spell_awaiting: bool = False
    error: Optional[str] = None


# --- Letter-practice exercise -------------------------------------------------


class LetterShowRequest(BaseModel):
    # Single A-Z letter to raise on the Braille cell.
    letter: str = Field(min_length=1, max_length=1, pattern=r"^[A-Za-z]$")


class LetterState(BaseModel):
    active: bool
    dry_run: bool = False
    letter: Optional[str] = None
    dots: list[int] = []
    # idle | showing | holding | lowering
    phase: str = "idle"
    repeats: int = 0


# --- Storytelling session -----------------------------------------------------


class StoryState(BaseModel):
    active: bool
    dry_run: bool = False
    # idle | greeting | listening | thinking | telling | quiz | spelling
    #   | ready | done | error
    phase: str = "idle"
    # Latest line Moo spoke aloud, or a short status note for the phone.
    message: Optional[str] = None
    # Full accumulated story (every part so far), kept coherent across extensions.
    story: Optional[str] = None
    parts: int = 0
    spelling_word: Optional[str] = None
    # Interactive spelling progress (set only while phase == "spelling").
    spell_letter: Optional[str] = None     # current letter on the cell
    spell_index: Optional[int] = None      # 0-based position in the word
    spell_total: Optional[int] = None      # letter count of the word
    spell_dots_left: Optional[int] = None  # raised dots not yet lowered
    spell_awaiting: bool = False           # True while waiting for the child
    # Which word game runs: spell | name | build, or None between games.
    game_mode: Optional[str] = None
    # Dots the child has raised so far in the build game, 0-5.
    build_raised: list[int] = []
    # Dots that make the letter in the build game, 0-5.
    build_target: list[int] = []
    error: Optional[str] = None
