"""Braille driver entry point.

Single backend: the Pi drives the 6 TB6612 H-bridge channels directly from
GPIO (12 direction lines + 6 button inputs). PWM and STBY are hardwired to
3.3V on the boards; there are no touch sensors.

`make_driver(config, dry_run=...)` builds a `GpioBrailleDriver` from the
optional pin overrides under `config.braille.gpio`. assistant/main.py uses
it through the `BrailleDriver` Protocol.
"""

from __future__ import annotations

from typing import Any, Protocol

from .braille_gpio import GpioBrailleDriver, GpioPinMap


class BrailleDriver(Protocol):
    def send_letter(self, letter: str) -> None: ...
    def send_word(self, word: str, letter_pause_seconds: float = 3.0) -> None: ...
    def close(self) -> None: ...


def make_driver(config: dict[str, Any], dry_run: bool = False) -> BrailleDriver:
    pin_cfg = (config.get("braille") or {}).get("gpio", {})
    defaults = GpioPinMap()

    motor_pins = pin_cfg.get("motor_pins")
    if motor_pins:
        motor_pins = tuple(tuple(pair) for pair in motor_pins)
    else:
        motor_pins = defaults.motor_pins

    button_pins = tuple(pin_cfg.get("button_pins", defaults.button_pins))
    stby_pin = int(pin_cfg.get("stby_pin", defaults.stby_pin))

    pins = GpioPinMap(
        motor_pins=motor_pins, button_pins=button_pins, stby_pin=stby_pin
    )
    return GpioBrailleDriver(pins=pins, dry_run=dry_run)
