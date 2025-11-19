"""StudyGuardian entry point for camera stream ingestion."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import cv2
import yaml
from loguru import logger

from camera_ingest import CameraStream, FrameSaveConfig, FrameSaver
from face_service import FaceMatch, FaceService
from posture_service import PostureConfig, PostureService


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


def build_face_service(root: Path, config: Dict[str, Any]) -> FaceService:
    known_dir = Path(config.get("known_dir", "data/known"))
    tolerance = float(config.get("tolerance", 0.55))
    location_model = config.get("location_model", "hog")
    service = FaceService.from_known_directory(
        root / known_dir, tolerance=tolerance, location_model=location_model
    )
    return service


def build_posture_service(config: Dict[str, Any]) -> PostureService:
    posture_config = PostureConfig(
        nose_drop=float(config.get("nose_drop", 0.12)),
        neck_angle=float(config.get("neck_angle", 45)),
    )
    return PostureService(posture_config)


def make_frame_handler(face_service: FaceService, posture_service: PostureService) -> Callable[[cv2.Mat], bool]:
    def handler(frame: "cv2.Mat") -> bool:
        matches: list[FaceMatch] = face_service.recognize(frame)
        identity = "unknown"
        if matches:
            primary = matches[0]
            identity = primary.identity
            if identity == "unknown":
                logger.debug("Unknown person detected (dist %.2f)", primary.distance)
            else:
                logger.info("Recognized %s (dist %.2f)", identity, primary.distance)
        else:
            logger.debug("No faces detected in current frame")

        posture = posture_service.analyze(frame)
        if posture:
            if posture.bad:
                logger.warning(
                    "Bad posture (%.3f drop / %.1f°) detected for %s: %s",
                    posture.nose_drop,
                    posture.neck_angle,
                    identity,
                    ", ".join(posture.reasons),
                )
            else:
                logger.debug("Posture looks good (%.3f drop / %.1f°) for %s", posture.nose_drop, posture.neck_angle, identity)
        else:
            logger.debug("Posture not available for %s", identity)

        return True

    return handler


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    settings_path = root / "config" / "settings.yaml"
    settings = load_settings(settings_path)

    configure_logger(root, settings.get("logging", {}))
    logger.info("Starting StudyGuardian camera ingest")

    frame_saver = build_frame_saver(root, settings.get("frame_save", {}))

    face_service = build_face_service(root, settings.get("face_recognition", {}))
    posture_service = build_posture_service(settings.get("posture", {}))

    capture_cfg = settings.get("capture", {})
    stream = CameraStream(
        source=settings.get("camera_url", ""),
        target_fps=float(capture_cfg.get("target_fps", 15)),
        reconnect_delay=float(capture_cfg.get("reconnect_delay", 5)),
        max_retries=int(capture_cfg.get("max_retries", 3)),
        frame_saver=frame_saver,
    )

    try:
        stream.iterate(on_frame=make_frame_handler(face_service, posture_service))
    except KeyboardInterrupt:
        logger.info("Interrupted, shutting down")
    except Exception as exc:  # pragma: no cover - runtime concerns
        logger.exception("Stream ingestion failed: {}", exc)
    finally:
        stream.release()
        posture_service.close()


if __name__ == "__main__":
    main()
