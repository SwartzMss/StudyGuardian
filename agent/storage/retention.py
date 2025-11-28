"""Background retention worker for face captures."""

from __future__ import annotations

import threading
from typing import Optional

from loguru import logger

from .postgres import Storage


class FaceCaptureRetentionWorker:
    """Periodically prune face_captures table by age and/or row count."""

    def __init__(
        self,
        storage: Storage,
        max_rows: Optional[int] = None,
        max_age_days: Optional[float] = None,
        interval_seconds: float = 600.0,
    ) -> None:
        self._storage = storage
        self._max_rows = max_rows
        self._max_age_days = max_age_days
        self._interval = interval_seconds
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        if self._max_rows is None and self._max_age_days is None:
            logger.info("Face capture retention disabled; no limits configured")
            return
        logger.info(
            "Starting face capture retention worker (max_rows={}, max_age_days={}, interval={}s)",
            self._max_rows,
            self._max_age_days,
            self._interval,
        )
        self._thread.start()

    def stop(self) -> None:
        if not self._thread.is_alive():
            return
        self._stop_event.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                deleted = self._storage.prune_face_captures(
                    max_rows=self._max_rows, max_age_days=self._max_age_days
                )
                if deleted:
                    logger.info("Pruned {} face capture record(s)", deleted)
            except Exception as exc:  # pragma: no cover - runtime safeguard
                logger.warning("Face capture retention failed: {}", exc)
            self._stop_event.wait(self._interval)
