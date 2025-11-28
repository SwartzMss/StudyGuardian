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

    def prune_face_captures(
        self,
        max_rows: int | None = None,
        max_age_days: float | None = None,
    ) -> int:
        """Delete old face captures by age or keep-most-recent row count."""
        cursor = self._conn.cursor()
        deleted = 0

        if max_age_days is not None:
            cursor.execute(
                f"DELETE FROM {self._face_table} "
                "WHERE timestamp < (NOW() - (%s || ' days')::interval)",
                (max_age_days,),
            )
            deleted += cursor.rowcount

        if max_rows is not None and max_rows > 0:
            cursor.execute(f"SELECT COUNT(*) FROM {self._face_table}")
            count = cursor.fetchone()[0] or 0
            if count > max_rows:
                to_delete = count - max_rows
                cursor.execute(
                    f"DELETE FROM {self._face_table} "
                    "WHERE id IN ("
                    "  SELECT id FROM {table} ORDER BY timestamp ASC LIMIT %s"
                    ")".format(table=self._face_table),
                    (to_delete,),
                )
                deleted += cursor.rowcount

        self._conn.commit()
        cursor.close()
        return deleted

    def _ensure_tables(self) -> None:
        cursor = self._conn.cursor()
        cursor.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')
        cursor.execute(self._create_face_table_sql())
        cursor.execute(self._create_posture_table_sql())
        self._ensure_posture_fk_cascade(cursor)
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
        face_capture_id: str | None = None,
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
    ) -> str:
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
        return str(face_id)

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
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  identity TEXT NOT NULL,
  group_tag TEXT NOT NULL,
  face_distance DOUBLE PRECISION,
  frame_path TEXT,
  timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
"""

    def _create_posture_table_sql(self) -> str:
        return f"""
CREATE TABLE IF NOT EXISTS {self._posture_table} (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  identity TEXT NOT NULL,
  is_bad BOOLEAN NOT NULL,
  nose_drop DOUBLE PRECISION,
  neck_angle DOUBLE PRECISION,
  reasons TEXT,
  face_distance DOUBLE PRECISION,
  frame_path TEXT,
  face_capture_id UUID,
  timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
"""

    def _ensure_posture_fk_cascade(self, cursor) -> None:
        """Ensure posture table FK cascades when face capture rows are deleted."""
        posture_table = self._posture_table
        face_table = self._face_table

        cursor.execute(
            """
            SELECT
              tc.constraint_name,
              rc.delete_rule
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.referential_constraints rc
              ON rc.constraint_name = tc.constraint_name
             AND rc.constraint_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_name = %s
              AND kcu.column_name = 'face_capture_id';
            """,
            (posture_table,),
        )
        row = cursor.fetchone()
        constraint_name = row[0] if row else None
        delete_rule = row[1] if row else None

        needs_update = constraint_name is None or delete_rule != "CASCADE"
        if not needs_update:
            return

        if constraint_name:
            cursor.execute(
                f'ALTER TABLE {posture_table} DROP CONSTRAINT "{constraint_name}";'
            )

        cursor.execute(
            f"""
            ALTER TABLE {posture_table}
            ADD CONSTRAINT fk_posture_face_capture
            FOREIGN KEY (face_capture_id)
            REFERENCES {face_table}(id)
            ON DELETE CASCADE;
            """
        )
