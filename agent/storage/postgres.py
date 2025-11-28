"""Posture event storage powered by PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class StorageConfig:
    postgres_dsn: str
    posture_table: str = "posture_events"
    face_table: str = "face_captures"
    reset_on_start: bool = False


class Storage:
    def __init__(self, config: StorageConfig) -> None:
        try:
            import psycopg2  # type: ignore[import]
        except ImportError as exc:  # pragma: no cover - runtime helper
            raise RuntimeError(
                "psycopg2-binary is required for PostgreSQL storage"
            ) from exc

        if not config.postgres_dsn:
            raise ValueError("PostgreSQL DSN must be provided")

        self._conn = psycopg2.connect(config.postgres_dsn)
        self._posture_table = config.posture_table
        self._face_table = config.face_table
        self._param = "%s"
        self._reset_on_start = bool(config.reset_on_start)
        if self._reset_on_start:
            self.reset()
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        cursor = self._conn.cursor()
        cursor.execute(self._create_face_table_sql())
        cursor.execute(self._create_posture_table_sql())
        cursor.execute(
            f"""
ALTER TABLE {self._posture_table}
ADD COLUMN IF NOT EXISTS face_capture_id INTEGER
    REFERENCES {self._face_table}(id) ON DELETE SET NULL
"""
        )
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
        face_capture_id: int | None = None,
    ) -> None:
        columns = [
            "identity",
            "is_bad",
            "nose_drop",
            "neck_angle",
            "reasons",
            "face_distance",
            "frame_path",
            "face_capture_id",
        ]
        placeholders = ", ".join([self._param] * len(columns))
        query = (
            f"INSERT INTO {self._posture_table} "
            f"({', '.join(columns)}) VALUES ({placeholders})"
        )
        cursor = self._conn.cursor()
        cursor.execute(
            query,
            (
                identity,
                is_bad,
                nose_drop,
                neck_angle,
                ", ".join(reasons),
                face_distance,
                frame_path,
                face_capture_id,
            ),
        )
        cursor.close()
        self._conn.commit()

    def log_face_capture(
        self,
        identity: str,
        group_tag: str,
        face_distance: float | None,
        frame_path: str | None,
    ) -> int:
        columns = ["identity", "group_tag", "face_distance", "frame_path"]
        placeholders = ", ".join([self._param] * len(columns))
        query = (
            f"INSERT INTO {self._face_table} "
            f"({', '.join(columns)}) VALUES ({placeholders}) RETURNING id"
        )
        cursor = self._conn.cursor()
        cursor.execute(query, (identity, group_tag, face_distance, frame_path))
        face_id = cursor.fetchone()[0]
        cursor.close()
        self._conn.commit()
        return int(face_id)

    def reset(self) -> None:
        self._drop_table(self._posture_table)
        self._drop_table(self._face_table)
        self._ensure_tables()

    def _drop_table(self, table_name: str) -> None:
        cursor = self._conn.cursor()
        cursor.execute(f"DROP TABLE IF EXISTS {table_name}")
        cursor.close()
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _create_face_table_sql(self) -> str:
        return f"""
CREATE TABLE IF NOT EXISTS {self._face_table} (
  id SERIAL PRIMARY KEY,
  identity TEXT NOT NULL,
  group_tag TEXT NOT NULL,
  face_distance DOUBLE PRECISION,
  frame_path TEXT,
  timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
"""

    def _create_posture_table_sql(self) -> str:
        return f"""
CREATE TABLE IF NOT EXISTS {self._posture_table} (
  id SERIAL PRIMARY KEY,
  identity TEXT NOT NULL,
  is_bad BOOLEAN NOT NULL,
  nose_drop DOUBLE PRECISION,
  neck_angle DOUBLE PRECISION,
  reasons TEXT,
  face_distance DOUBLE PRECISION,
  frame_path TEXT,
  face_capture_id INTEGER REFERENCES {self._face_table}(id) ON DELETE SET NULL,
  timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
"""
