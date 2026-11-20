"""Pi-direct GPIO driver for the Moo Braille cell.

Hardware — everything wired straight to Pi GPIO, no I2C expander:

    6 × TB6612FNG H-bridge channels, one per solenoid:
        AIN1, AIN2  → Pi GPIO  (direction — 12 lines total)
        PWM         → tied to 3.3V on the board (motors always full speed)
        STBY        → Pi GPIO  (all 3 boards' STBY tied to one pin)
    6 × push buttons → Pi GPIO  (input, internal pull-up)

STBY is on a GPIO — not hardwired to 3.3V — and the pin chosen defaults to
pull-DOWN. So whenever this driver is not running (boot, between tests,
unconfigured device), STBY floats LOW and every TB6612 output is disabled,
regardless of what the direction GPIOs are doing. The driver only raises
STBY once all direction pins are confirmed LOW.

There are no touch sensors. Behavior:

  - send_letter(L): for each dot in LETTER_DOTS[L], drive that motor
    "forward" (raise the dot). A background tick stops the motor after
    MOTOR_RUN_SECONDS so it doesn't run forever.
  - Pressing button N drives motor N "backward" (lower the dot) and is also
    recorded for pop_last_button() — the child clearing a felt dot, exactly
    as in the calibration wizard.
  - send_word(W) raises each letter, waits letter_pause_seconds for the
    child to feel and clear it, then moves on.

Direction logic per TB6612 channel:
    forward  → AIN1=HIGH, AIN2=LOW
    backward → AIN1=LOW,  AIN2=HIGH
    stop     → AIN1=LOW,  AIN2=LOW   (coast)

Pin assignments come from config.braille.gpio (see assistant/braille.py).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from .braille_letters import dots_for

logger = logging.getLogger(__name__)

MOTOR_COUNT = 6
# HARD SAFETY CAP. A motor must never stay energized longer than this — a
# stuck-on motor overheats and can melt the casing. The watchdog (_loop)
# force-stops any motor past this, and a re-command in the same direction
# does NOT extend it, so the cap is measured from the first activation.
# Keep this well under 2 seconds.
MOTOR_RUN_SECONDS = 1.5
# Motors are powered through the Pi's 5V rail, so stagger their start times
# to keep inrush current spikes from stacking and browning out the Pi.
MOTOR_STAGGER_SECONDS = 0.15
# A button press lowers its motor, but motor noise can glitch the button
# lines into phantom presses. Allow only one button-driven lowering per
# motor per this interval, so phantoms can't re-energize a motor in a loop.
BUTTON_RELOWER_MIN_SECONDS = 2.5


@dataclass
class GpioPinMap:
    # (IN1, IN2) BCM pin pair for each of the 6 motors.
    # IN1 runs down the left header column top-to-bottom (GPIO 4, 17, 27, 22,
    # 10, 9); IN2 runs down the right column top-to-bottom (GPIO 14, 15, 18,
    # 23, 24, 25).
    motor_pins: tuple[tuple[int, int], ...] = (
        (4, 14),    # motor 0 — phys pins 7, 8
        (17, 15),   # motor 1 — phys pins 11, 10
        (27, 18),   # motor 2 — phys pins 13, 12
        (22, 23),   # motor 3 — phys pins 15, 16
        (10, 24),   # motor 4 — phys pins 19, 18
        (9, 25),    # motor 5 — phys pins 21, 22
    )
    # One push button per motor. Buttons 0-4 take the remaining left-column
    # pins (GPIO 11, 5, 6, 13, 19); button 5 spills to GPIO16 on the right.
    # Phys pins: 23, 29, 31, 33, 35, 36.
    button_pins: tuple[int, ...] = (11, 5, 6, 13, 19, 16)
    # TB6612 STBY (all 3 boards tied together) — GPIO26, phys pin 37.
    # GPIO26 defaults to pull-down, so the drivers are disabled until our
    # code explicitly enables them.
    stby_pin: int = 26


@dataclass
class _MotorState:
    direction: int = 0            # +1 up, -1 down, 0 stopped
    start_time: float = 0.0       # when the current run began (watchdog clock)
    last_button_lower: float = 0.0  # last time a button press lowered this motor


class GpioBrailleDriver:
    """Drives the 6-cell Braille display via direct Pi GPIO + TB6612 boards."""

    def __init__(self, pins: GpioPinMap | None = None, dry_run: bool = False) -> None:
        self.pins = pins or GpioPinMap()
        self.dry_run = dry_run
        self._motors = [_MotorState() for _ in range(MOTOR_COUNT)]
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._ain1: list = []   # gpiozero OutputDevice per motor
        self._ain2: list = []
        self._buttons: list = []
        self._stby = None       # gpiozero OutputDevice — TB6612 enable
        self._last_button: int | None = None
        self._button_lock = threading.Lock()
        # Every press in order, so two quick presses are never collapsed into
        # one. Read with pop_presses(); pop_last_button() is kept for the
        # calibration wizard, which only needs the most recent press.
        self._presses: deque[tuple[int, float]] = deque(maxlen=64)
        # The button check turns this off so pressing a button that is
        # already down does not drive its motor against the end stop.
        self.lower_on_press = True

        if self.dry_run:
            logger.info("GpioBrailleDriver running in dry-run mode")
            return

        self._init_hardware()
        if self.dry_run:
            logger.info("GpioBrailleDriver staying in dry-run after hardware probe failure")
            return

        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    # ------------------------------------------------------------------ setup

    def _init_hardware(self) -> None:
        try:
            from gpiozero import Button, OutputDevice
        except ImportError as exc:
            logger.warning("gpiozero missing (%s); falling back to dry-run", exc)
            self.dry_run = True
            return

        try:
            # STBY first, held LOW — drivers disabled until everything is ready.
            self._stby = OutputDevice(
                self.pins.stby_pin, active_high=True, initial_value=False
            )
            for ain1, ain2 in self.pins.motor_pins:
                self._ain1.append(OutputDevice(ain1, active_high=True, initial_value=False))
                self._ain2.append(OutputDevice(ain2, active_high=True, initial_value=False))
            self._buttons = [
                Button(pin, pull_up=True, bounce_time=0.05)
                for pin in self.pins.button_pins
            ]
            for idx, btn in enumerate(self._buttons):
                btn.when_pressed = lambda i=idx: self._on_button(i)
            # All 12 direction pins are confirmed LOW — now safe to enable.
            self._stby.on()
        except Exception as exc:
            logger.warning("GPIO init failed (%s); falling back to dry-run", exc)
            self._tear_down_partial()
            self.dry_run = True

    def _tear_down_partial(self) -> None:
        if self._stby is not None:
            try:
                self._stby.off()
            except Exception:
                pass
        for dev in (*self._ain1, *self._ain2, *self._buttons):
            try:
                dev.close()
            except Exception:
                pass
        if self._stby is not None:
            try:
                self._stby.close()
            except Exception:
                pass
        self._ain1 = []
        self._ain2 = []
        self._buttons = []
        self._stby = None

    # ----------------------------------------------------------- motor control

    def _cmd_forward(self, i: int) -> None:
        if self.dry_run:
            return
        with self._lock:
            m = self._motors[i]
            self._ain1[i].on()
            self._ain2[i].off()
            # Only (re)start the watchdog clock when ENTERING a forward run.
            # A repeat command while already running forward must not extend
            # the run — that is exactly how a motor gets stuck on.
            if m.direction != 1:
                m.start_time = time.monotonic()
            m.direction = 1

    def _cmd_backward(self, i: int) -> None:
        if self.dry_run:
            return
        with self._lock:
            m = self._motors[i]
            self._ain1[i].off()
            self._ain2[i].on()
            if m.direction != -1:
                m.start_time = time.monotonic()
            m.direction = -1

    def _cmd_stop(self, i: int) -> None:
        if self.dry_run:
            return
        with self._lock:
            self._ain1[i].off()
            self._ain2[i].off()
            self._motors[i].direction = 0

    def _loop(self) -> None:
        """Watchdog: force-stop any motor energized past MOTOR_RUN_SECONDS.

        Wrapped in try/except so a transient GPIO error can never kill the
        watchdog thread — a motor left running indefinitely is a melt hazard.
        """
        while not self._stop.is_set():
            try:
                now = time.monotonic()
                for i in range(MOTOR_COUNT):
                    m = self._motors[i]
                    if m.direction != 0 and (now - m.start_time) >= MOTOR_RUN_SECONDS:
                        self._cmd_stop(i)
            except Exception:
                logger.exception("motor watchdog tick error")
            time.sleep(0.05)

    def _on_button(self, i: int) -> None:
        """Button i pressed → record it (pop_last_button) and lower motor i.

        The press is always recorded. Lowering motor i is rate-limited per
        motor: motor current spikes can glitch the button lines into phantom
        presses, and lowering on every one would re-energize the motor in a
        feedback loop. Only one button-driven lowering per motor per
        BUTTON_RELOWER_MIN_SECONDS is allowed; the watchdog still caps the run.
        """
        now = time.monotonic()
        with self._button_lock:
            self._last_button = i
            self._presses.append((i, now))
        if not self.lower_on_press:
            logger.info("button %d pressed", i)
            return
        m = self._motors[i]
        if now - m.last_button_lower >= BUTTON_RELOWER_MIN_SECONDS:
            m.last_button_lower = now
            logger.info("button %d pressed → lowering motor %d", i, i)
            self._cmd_backward(i)
        else:
            logger.info("button %d pressed (lowering rate-limited)", i)

    def pop_last_button(self) -> int | None:
        """Return the most recently pressed button index, then clear it.

        Used by the calibration wizard to learn which physical button maps
        to which motor.
        """
        with self._button_lock:
            b = self._last_button
            self._last_button = None
            return b

    def pop_presses(self) -> list[tuple[int, float]]:
        """Return every press since the last call, oldest first, then clear them.

        Each entry is (button index, time.monotonic() of the press).
        """
        with self._button_lock:
            out = list(self._presses)
            self._presses.clear()
            self._last_button = None
            return out

    # --------------------------------------------------------------- interface

    def send_letter(self, letter: str) -> None:
        dots = dots_for(letter)
        if not dots:
            return
        if self.dry_run:
            logger.info("[dry-run] gpio letter %s → motors %s", letter, dots)
            return
        # Stagger starts so inrush spikes don't stack on the shared 5V rail.
        for n, motor_idx in enumerate(dots):
            self._cmd_forward(motor_idx)
            if n < len(dots) - 1:
                time.sleep(MOTOR_STAGGER_SECONDS)

    def send_word(self, word: str, letter_pause_seconds: float = 3.0) -> None:
        for letter in word:
            if not letter.isalpha():
                continue
            self.send_letter(letter)
            time.sleep(letter_pause_seconds)

    def show_letter(self, letter: str, hold_seconds: float = 5.0) -> None:
        """Raise the dots for one letter, hold them, then lower them again.

        Used by the letter-practice exercise: the child feels the raised
        letter for `hold_seconds`, then every dot retracts so the cell is
        clear and ready to repeat. Blocks for the full cycle, so callers
        run it on a background thread.
        """
        dots = dots_for(letter)
        if not dots:
            return
        if self.dry_run:
            logger.info("[dry-run] show letter %s -> dots %s", letter, dots)
            return
        for n, idx in enumerate(dots):
            self._cmd_forward(idx)
            if n < len(dots) - 1:
                time.sleep(MOTOR_STAGGER_SECONDS)
        time.sleep(max(0.0, hold_seconds))
        for n, idx in enumerate(dots):
            self._cmd_backward(idx)
            if n < len(dots) - 1:
                time.sleep(MOTOR_STAGGER_SECONDS)

    def render_letter(self, letter: str) -> None:
        """Raise the dots for `letter`, lower every other dot, and leave them.

        No hold and no timed lowering — the dots stay set until the cell is
        rendered again or a button lowers one. Used by both the letter
        exercise and story spelling so they behave identically.
        """
        wanted = set(dots_for(letter))
        if self.dry_run:
            logger.info("[dry-run] render letter %s -> %s", letter, sorted(wanted))
            return
        for i in range(MOTOR_COUNT):
            if i in wanted:
                self._cmd_forward(i)
            else:
                self._cmd_backward(i)
            time.sleep(MOTOR_STAGGER_SECONDS)

    def reset_cell(self) -> None:
        """Drive every dot down so the cell is left flat.

        Calibration pulses motors and can leave dots raised; this lowers
        them all and waits for them to finish travelling before the caller
        closes the driver.
        """
        if self.dry_run:
            logger.info("[dry-run] reset cell — all dots down")
            return
        for i in range(MOTOR_COUNT):
            self._cmd_backward(i)
            time.sleep(MOTOR_STAGGER_SECONDS)
        time.sleep(MOTOR_RUN_SECONDS)
        for i in range(MOTOR_COUNT):
            self._cmd_stop(i)

    def test_motor(self, motor_index: int, hold_seconds: float = 1.0) -> None:
        """Pulse one motor: raise for hold_seconds, stop, lower for hold_seconds, stop."""
        if not 0 <= motor_index < MOTOR_COUNT:
            raise ValueError(f"motor_index must be 0..{MOTOR_COUNT - 1}")
        if self.dry_run:
            logger.info("[dry-run] test motor %d", motor_index)
            return
        self._cmd_forward(motor_index)
        time.sleep(hold_seconds)
        self._cmd_stop(motor_index)
        time.sleep(0.3)
        self._cmd_backward(motor_index)
        time.sleep(hold_seconds)
        self._cmd_stop(motor_index)

    def test_all_motors(self, hold_seconds: float = 1.0) -> list[int]:
        tested: list[int] = []
        for i in range(MOTOR_COUNT):
            self.test_motor(i, hold_seconds=hold_seconds)
            tested.append(i)
        return tested

    def raise_motor(self, motor_index: int) -> None:
        """Drive one motor 'up' and return immediately. The background tick
        stops it after MOTOR_RUN_SECONDS; the pin stays raised so the user
        can see which dot it is (used by button calibration)."""
        if not 0 <= motor_index < MOTOR_COUNT:
            raise ValueError(f"motor_index must be 0..{MOTOR_COUNT - 1}")
        if self.dry_run:
            logger.info("[dry-run] raise motor %d", motor_index)
            return
        self._cmd_forward(motor_index)

    def lower_motor(self, motor_index: int) -> None:
        """Drive one motor 'down' and return immediately."""
        if not 0 <= motor_index < MOTOR_COUNT:
            raise ValueError(f"motor_index must be 0..{MOTOR_COUNT - 1}")
        if self.dry_run:
            logger.info("[dry-run] lower motor %d", motor_index)
            return
        self._cmd_backward(motor_index)

    def close(self) -> None:
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=1.0)
        if self.dry_run:
            return
        for i in range(MOTOR_COUNT):
            try:
                self._cmd_stop(i)
            except Exception:
                pass
        # _tear_down_partial drops STBY LOW and releases every pin.
        self._tear_down_partial()
