"""Posture event storage powered by PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class StorageConfig:
    postgres_dsn: str
    table_name: str = "posture_events"


class Storage:
    def __init__(self, config: StorageConfig) -> None:
        try:
            import psycopg2  # type: ignore[import]
        except ImportError as exc:  # pragma: no cover - runtime helper
            raise RuntimeError("psycopg2-binary is required for PostgreSQL storage") from exc

        if not config.postgres_dsn:
            raise ValueError("PostgreSQL DSN must be provided")

        self._conn = psycopg2.connect(config.postgres_dsn)
        self._table = config.table_name
        self._param = "%s"
        self._ensure_table()

    def _ensure_table(self) -> None:
        cursor = self._conn.cursor()
        cursor.execute(self._create_table_sql())
        cursor.close()
        self._conn.commit()

    def log_posture(
        self,
        identity: str,
        is_bad: bool,
        nose_drop: float,
        neck_angle: float,
        reasons: List[str],
        face_distance: float | None = None,
        frame_path: str | None = None,
    ) -> None:
        payload = (
            identity,
            is_bad,
            nose_drop,
            neck_angle,
            ", ".join(reasons),
            face_distance,
            frame_path,
        )
        placeholders = ", ".join([self._param] * len(payload))
        query = (
            f"INSERT INTO {self._table} "
            "(identity, is_bad, nose_drop, neck_angle, reasons, face_distance, frame_path) "
            f"VALUES ({placeholders})"
        )
        cursor = self._conn.cursor()
        cursor.execute(query, payload)
        cursor.close()
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _create_table_sql(self) -> str:
        return f"""
CREATE TABLE IF NOT EXISTS {self._table} (
  id SERIAL PRIMARY KEY,
  identity TEXT NOT NULL,
  is_bad BOOLEAN NOT NULL,
  nose_drop DOUBLE PRECISION,
  neck_angle DOUBLE PRECISION,
  reasons TEXT,
  face_distance DOUBLE PRECISION,
  frame_path TEXT,
  timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
"""
