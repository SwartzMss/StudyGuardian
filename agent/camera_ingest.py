"""Video ingestion helpers for StudyGuardian."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import cv2
from loguru import logger


@dataclass
class FrameSaveConfig:
    root: Path
    enabled: bool = False
    interval_seconds: float = 30.0
    default_category: str = "raw"

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / self.default_category).mkdir(parents=True, exist_ok=True)


class FrameSaver:
    """Save periodic frames into category folders for offline inspection."""

    def __init__(self, config: FrameSaveConfig) -> None:
        self._config = config
        self._last_saved = 0.0

    def save(self, frame: "cv2.Mat", category: Optional[str] = None) -> Optional[Path]:
        if not self._config.enabled:
            return None

        now = time.time()
        if now - self._last_saved < self._config.interval_seconds:
            return None

        category = category or self._config.default_category
        target_dir = self._config.root / category
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".jpg"
        path = target_dir / filename
        if cv2.imwrite(str(path), frame):
            self._last_saved = now
            logger.debug("Saved frame snapshot to {}", path)
            return path

        logger.warning("Unable to save frame to {}", path)
        return None


class CameraStream:
    """Maintain a connection to a video stream and deliver frames."""

    def __init__(
        self,
        source: str,
        target_fps: Optional[float] = None,
        reconnect_delay: float = 5.0,
        max_retries: int = 3,
        frame_saver: Optional[FrameSaver] = None,
    ) -> None:
        self._source = source
        self._target_fps = target_fps
        self._reconnect_delay = reconnect_delay
        self._max_retries = max_retries
        self._frame_saver = frame_saver
        self._capture: Optional[cv2.VideoCapture] = None

    def __enter__(self) -> CameraStream:
        self._open_capture()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()

    def _open_capture(self) -> None:
        if self._capture is not None:
            self._capture.release()

        self._capture = cv2.VideoCapture(self._source)
        attempt = 0
        while not self._capture.isOpened():
            attempt += 1
            if attempt > self._max_retries:
                raise RuntimeError(f"Unable to open stream after {self._max_retries} attempts")
            logger.warning("Stream not available, retrying in {}s", self._reconnect_delay)
            time.sleep(self._reconnect_delay)
            self._capture.open(self._source)

        logger.info("Connected to stream {}", self._source)

    def release(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def _read_raw_frame(self) -> Optional["cv2.Mat"]:
        if self._capture is None or not self._capture.isOpened():
            self._open_capture()

        ret, frame = self._capture.read()
        if not ret or frame is None:
            logger.warning("Failed to read frame from stream, reconnecting after delay")
            time.sleep(self._reconnect_delay)
            self._open_capture()
            return None

        return frame

    def iterate(
        self,
        on_frame: Optional[Callable[["cv2.Mat"], bool]] = None,
        max_frames: Optional[int] = None,
    ) -> None:
        """Start consuming the stream and handing frames to a callback."""

        frames = 0
        frame_interval = 1.0 / self._target_fps if self._target_fps and self._target_fps > 0 else 0
        last_time = time.time()

        with self:
            while max_frames is None or frames < max_frames:
                frame = self._read_raw_frame()
                if frame is None:
                    continue

                frames += 1
                logger.debug("Captured frame #{}", frames)

                if self._frame_saver is not None:
                    self._frame_saver.save(frame)

                if on_frame:
                    continue_loop = on_frame(frame)
                    if continue_loop is False:
                        logger.info("Frame callback requested shutdown")
                        break

                now = time.time()
                elapsed = now - last_time
                delay = frame_interval - elapsed
                if delay > 0:
                    time.sleep(delay)
                last_time = time.time()

        logger.info("Stream iteration ended after {} frames", frames)
