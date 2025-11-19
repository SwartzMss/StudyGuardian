"""Storage helpers for StudyGuardian events."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class StorageConfig:
    backend: str = "sqlite"
    sqlite_path: Path = Path("data/db/guardian.db")
    postgres_dsn: Optional[str] = None
    table_name: str = "posture_events"


class Storage:
    def __init__(self, config: StorageConfig) -> None:
        self._backend = config.backend.lower()
        self._table = config.table_name

        if self._backend == "postgres":
            try:
                import psycopg2  # type: ignore[import]
            except ImportError as exc:  # pragma: no cover - runtime helper
                raise RuntimeError("psycopg2-binary is required for PostgreSQL storage") from exc

            if not config.postgres_dsn:
                raise ValueError("PostgreSQL DSN must be provided when using postgres backend")

            self._conn = psycopg2.connect(config.postgres_dsn)
            self._param = "%s"
            self._create_table_sql = self._postgres_create_statement()
        else:
            import sqlite3  # type: ignore[import]

            path = config.sqlite_path
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(path, check_same_thread=False)
            self._param = "?"
            self._create_table_sql = self._sqlite_create_statement()

        self._ensure_table()

    def _ensure_table(self) -> None:
        cursor = self._conn.cursor()
        cursor.execute(self._create_table_sql)
        cursor.close()
        self._conn.commit()

    def log_posture(
        self,
        identity: str,
        is_bad: bool,
        nose_drop: float,
        neck_angle: float,
        reasons: List[str],
        face_distance: Optional[float] = None,
        frame_path: Optional[str] = None,
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

    def _sqlite_create_statement(self) -> str:
        return f"""
CREATE TABLE IF NOT EXISTS {self._table} (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  identity TEXT NOT NULL,
  is_bad BOOLEAN NOT NULL,
  nose_drop REAL,
  neck_angle REAL,
  reasons TEXT,
  face_distance REAL,
  frame_path TEXT,
  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

    def _postgres_create_statement(self) -> str:
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
