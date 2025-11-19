"""StudyGuardian entry point for camera stream ingestion."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

import cv2
import yaml
from loguru import logger

from camera_ingest import CameraStream, FrameSaveConfig, FrameSaver


def load_settings(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing configuration at {path}")

    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def configure_logger(root: Path, config: Dict[str, Any]) -> None:
    logger.remove()
    log_level = config.get("level", "INFO").upper()
    stderr = sys.stderr
    logger.add(stderr, level=log_level)

    log_file = Path(config.get("file", "logs/camera_ingest.log"))
    if not log_file.is_absolute():
        log_file = root / log_file

    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger.add(str(log_file), rotation="10 MB", level=log_level)


def build_frame_saver(root: Path, config: Dict[str, Any]) -> Optional[FrameSaver]:
    enabled = bool(config.get("enable", False))
    if not enabled:
        return None

    save_root = config.get("root", "data/captures")
    interval = float(config.get("interval_seconds", 30.0))
    default_category = config.get("default_category", "raw")
    save_root_path = root / save_root

    frame_config = FrameSaveConfig(
        root=save_root_path,
        enabled=True,
        interval_seconds=interval,
        default_category=default_category,
    )
    return FrameSaver(frame_config)


def handle_frame(frame: "cv2.Mat") -> bool:
    height, width = frame.shape[:2]
    logger.debug("Received frame of size %dx%d", width, height)
    # Placeholder: future modules (face, posture, alerts) will process the frame here.
    return True


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    settings_path = root / "config" / "settings.yaml"
    settings = load_settings(settings_path)

    configure_logger(root, settings.get("logging", {}))
    logger.info("Starting StudyGuardian camera ingest")

    frame_saver = build_frame_saver(root, settings.get("frame_save", {}))

    capture_cfg = settings.get("capture", {})
    stream = CameraStream(
        source=settings.get("camera_url", ""),
        target_fps=float(capture_cfg.get("target_fps", 15)),
        reconnect_delay=float(capture_cfg.get("reconnect_delay", 5)),
        max_retries=int(capture_cfg.get("max_retries", 3)),
        frame_saver=frame_saver,
    )

    try:
        stream.iterate(on_frame=handle_frame)
    except KeyboardInterrupt:
        logger.info("Interrupted, shutting down")
    except Exception as exc:  # pragma: no cover - runtime concerns
        logger.exception("Stream ingestion failed: {}", exc)
    finally:
        stream.release()


if __name__ == "__main__":
    main()
