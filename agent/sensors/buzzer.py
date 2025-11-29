"""Simple buzzer helper with optional lgpio support."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from loguru import logger

try:
    import lgpio
except ImportError:  # pragma: no cover - optional dependency
    lgpio = None


BeepFunc = Callable[[], None]


@dataclass
class BuzzerConfig:
    gpio_pin: Optional[int] = 24
    enable: bool = False


class Buzzer:
    """Simple buzzer interface supporting lgpio control."""

    def __init__(self, pin: Optional[int] = 24, beep_func: Optional[BeepFunc] = None):
        """Initialize the buzzer.

        Parameters
        ----------
        pin : int, optional
            GPIO pin number associated with the buzzer.
        beep_func : callable, optional
            Custom function to call for each beep. If not provided, will
            attempt to use :mod:`lgpio` if available, otherwise print.
        """
        self.pin = pin
        self._handle = None

        if beep_func is not None:
            self.beep_func = beep_func
        elif lgpio is not None and self.pin is not None:
            self._handle = lgpio.gpiochip_open(0)
            lgpio.gpio_claim_output(self._handle, self.pin)
            self.beep_func = self._lgpio_beep
        else:
            self.beep_func = self._print_beep

    def close(self) -> None:
        """Release lgpio resources if in use."""
        if self._handle is not None:
            lgpio.gpiochip_close(self._handle)
            self._handle = None

    def _print_beep(self) -> None:
        """Fallback beep action (console output)."""
        if self.pin is not None:
            print(f"Beep on pin {self.pin}!")
        else:
            print("Beep!")

    def _lgpio_beep(self) -> None:
        """Beep using lgpio."""
        lgpio.gpio_write(self._handle, self.pin, 1)
        time.sleep(0.1)
        lgpio.gpio_write(self._handle, self.pin, 0)

    def beep_times(self, times: int, interval: float = 0.5) -> None:
        """Beep a number of times with a delay between beeps."""
        for _ in range(times):
            self.beep_func()
            time.sleep(interval)


def build_buzzer(config: dict) -> Optional[Buzzer]:
    """Return an initialized buzzer (or None if disabled/misconfigured)."""
    if not config or not config.get("enable", False):
        return None
    try:
        pin_value = config.get("gpio_pin", 24)
        buzzer = Buzzer(pin=int(pin_value) if pin_value is not None else None)
        logger.info("Buzzer enabled on BCM {}", buzzer.pin)
        return buzzer
    except Exception as exc:  # pragma: no cover - hardware/runtime concerns
        logger.warning("Buzzer not started: {}", exc)
        return None
