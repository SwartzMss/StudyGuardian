"""HS-SR501 PIR sensor wrapper using gpiozero."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from loguru import logger

try:
    from gpiozero import MotionSensor
except Exception as exc:  # pragma: no cover - hardware dependency
    MotionSensor = None  # type: ignore
    _import_error = exc

MotionCallback = Callable[[bool], None]


@dataclass
class PIRConfig:
    gpio_pin: int
    enable: bool = True
    settle_seconds: float = 2.0


class PIRSensor:
    """Manage a PIR sensor lifecycle and callbacks."""

    def __init__(
        self, config: PIRConfig, on_motion: Optional[MotionCallback] = None
    ) -> None:
        if MotionSensor is None:
            raise ImportError("gpiozero is required for PIR support") from _import_error  # type: ignore[name-defined]

        self._config = config
        self._on_motion = on_motion
        self._sensor = MotionSensor(config.gpio_pin)

        logger.info(
            "PIR warming up for {:.1f}s on BCM {}",
            config.settle_seconds,
            config.gpio_pin,
        )
        if config.settle_seconds > 0:
            import time

            time.sleep(config.settle_seconds)
        logger.info("PIR ready on BCM {}", config.gpio_pin)

        self._sensor.when_motion = self._handle_motion
        self._sensor.when_no_motion = self._handle_no_motion

    def _handle_motion(self) -> None:  # pragma: no cover - hardware callbacks
        logger.info("Motion detected")
        if self._on_motion:
            self._on_motion(True)

    def _handle_no_motion(self) -> None:  # pragma: no cover - hardware callbacks
        logger.debug("Motion cleared")
        if self._on_motion:
            self._on_motion(False)

    def close(self) -> None:
        try:
            self._sensor.close()
        except Exception:
            pass


def build_pir_sensor(
    config: dict, on_motion: Optional[MotionCallback] = None
) -> Optional[PIRSensor]:
    """Return an initialized PIR sensor (or None if disabled/misconfigured)."""
    if not config or not config.get("enable", False):
        return None
    try:
        return PIRSensor(
            PIRConfig(
                gpio_pin=int(config.get("gpio_pin", 23)),
                enable=True,
                settle_seconds=float(config.get("settle_seconds", 2.0)),
            ),
            on_motion=on_motion,
        )
    except Exception as exc:
        logger.warning("PIR sensor not started: {}", exc)
        return None
