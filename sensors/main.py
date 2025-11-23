"""Sensor manager entrypoint. Loads enabled sensors from config and runs forever."""

from __future__ import annotations

import argparse
import signal
from pathlib import Path
from typing import Any, Dict, List

import yaml
from loguru import logger

from sensors.pir import build_pir_sensor


def load_settings(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing configuration at {path}")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def configure_logger(root: Path, config: Dict[str, Any]) -> None:
    logger.remove()
    log_level = config.get("level", "INFO").upper()
    logger.add(lambda msg: print(msg, end=""), level=log_level)  # stdout

    log_file = Path(config.get("file", "logs/sensors.log"))
    if not log_file.is_absolute():
        log_file = root / log_file
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger.add(str(log_file), rotation="10 MB", level=log_level)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="StudyGuardian sensor manager")
    parser.add_argument(
        "--config",
        default="config/settings.yaml",
        help="Path to settings YAML",
    )
    return parser.parse_args()


def build_sensors(settings: Dict[str, Any]) -> List[object]:
    sensors: List[object] = []
    pir_cfg = settings.get("pir_sensor") or {}
    sensor = build_pir_sensor(pir_cfg)
    if sensor:
        sensors.append(sensor)
    return sensors


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    settings = load_settings(root / args.config)

    configure_logger(root, settings.get("logging", {}))
    logger.info("Starting sensor manager")

    active_sensors = build_sensors(settings)
    if not active_sensors:
        logger.warning("No sensors active; exiting")
        return

    try:
        signal.pause()
    except KeyboardInterrupt:
        logger.info("Sensor manager interrupted, shutting down")
    finally:
        for sensor in active_sensors:
            try:
                close = getattr(sensor, "close", None)
                if callable(close):
                    close()
            except Exception:
                pass
        logger.info("Sensor manager stopped")


if __name__ == "__main__":
    main()
