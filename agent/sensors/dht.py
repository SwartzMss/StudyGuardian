"""DHT22 temperature/humidity helper using adafruit-circuitpython-dht."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Tuple

from loguru import logger

_import_error: Exception | None = None

try:
    import adafruit_dht  # type: ignore
    import board  # type: ignore
except Exception as exc:  # pragma: no cover - optional hardware dependency
    adafruit_dht = None  # type: ignore[assignment]
    board = None  # type: ignore[assignment]
    _import_error = exc


@dataclass
class DHT22Config:
    gpio_pin: int = 4
    enable: bool = False


class DHT22Sensor:
    """Lightweight wrapper around adafruit-circuitpython-dht."""

    def __init__(self, config: DHT22Config) -> None:
        if adafruit_dht is None or board is None:
            raise ImportError(
                "adafruit-circuitpython-dht and adafruit-blinka are required"
            ) from _import_error

        self._config = config
        self.pin = config.gpio_pin

        board_pin = getattr(board, f"D{self.pin}")
        self._sensor = adafruit_dht.DHT22(board_pin)

    def read(
        self,
        retries: int = 3,
        retry_delay: float = 1.0,
    ) -> Tuple[Optional[float], Optional[float]]:
        """Return humidity (RH %) and temperature (°C) or (None, None) on failure."""
        attempts = max(1, int(retries))
        for attempt in range(attempts):
            try:
                temperature = self._sensor.temperature
                humidity = self._sensor.humidity
                if temperature is None or humidity is None:
                    raise RuntimeError("Sensor returned None")
                return float(humidity), float(temperature)
            except RuntimeError as exc:  # pragma: no cover - hardware runtime
                logger.debug("DHT22 read failed: {}", exc)
            except Exception as exc:  # pragma: no cover - hardware runtime
                logger.debug("DHT22 read error: {}", exc)

            if attempt < attempts - 1:
                time.sleep(retry_delay)

        return None, None

    def loop(self, interval: float = 2.0):
        """Yield successive readings every interval seconds."""
        while True:
            yield self.read()
            time.sleep(interval)

    def close(self) -> None:
        """Release sensor resources."""
        try:
            self._sensor.exit()
        except AttributeError:
            pass
        except Exception:
            pass


def build_dht22_sensor(config: dict) -> Optional[DHT22Sensor]:
    """Return an initialized DHT22 sensor (or None if disabled/misconfigured)."""
    if not config or not config.get("enable", False):
        return None
    try:
        return DHT22Sensor(
            DHT22Config(
                gpio_pin=int(config.get("gpio_pin", 4)),
                enable=True,
            )
        )
    except Exception as exc:  # pragma: no cover - hardware/runtime concerns
        logger.warning("DHT22 sensor not started: {}", exc)
        return None
