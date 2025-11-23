"""Sensor management package (e.g., PIR)."""

from sensors.pir import PIRConfig, PIRSensor, build_pir_sensor

__all__ = ["PIRConfig", "PIRSensor", "build_pir_sensor"]
