"""StudyGuardian entry point for camera stream ingestion."""

from __future__ import annotations

import os
import sys
import threading
import time
from urllib.parse import urlparse
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Set, Tuple

import cv2
import yaml
from loguru import logger

# Quiet TFLite / cpuinfo warnings unless user overrides.
os.environ.setdefault("CPUINFO_LOG_LEVEL", "error")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "1")

from agent.capture import (
    CameraStream,
    FrameSaveConfig,
    FrameSaver,
    IdentityCapture,
    IdentityCaptureConfig,
)
from agent.posture import PostureConfig, PostureService
from agent.recognition import FaceMatch, FaceService
from agent.storage import Storage, StorageConfig
from agent.sensors import PIRSensor, build_pir_sensor


class MotionGate:
    """Gate frame processing based on PIR activity and face presence."""

    def __init__(
        self,
        idle_timeout_seconds: float,
    ) -> None:
        self._idle_timeout = idle_timeout_seconds
        self._lock = threading.Lock()
        self._active = False
        self._last_face_ts = 0.0

    def activate(self) -> float:
        """Enable processing window and reset idle timer."""
        now = time.monotonic()
        with self._lock:
            self._active = True
            self._last_face_ts = now  # start timeout countdown immediately
            return now

    def mark_face_seen(self) -> None:
        with self._lock:
            self._last_face_ts = time.monotonic()

    def deactivate(self) -> None:
        with self._lock:
            self._active = False

    def should_process(self) -> Tuple[bool, Optional[str]]:
        """Return (active, reason_if_disabled)."""
        now = time.monotonic()
        with self._lock:
            if not self._active:
                return False, None
            if (
                self._last_face_ts > 0
                and (now - self._last_face_ts) > self._idle_timeout
            ):
                self._active = False
                return False, f"no faces for {now - self._last_face_ts:.1f}s"
            return True, None


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

    log_file = root / "logs" / "agent.log"
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


def build_identity_capture(
    root: Path,
    config: Dict[str, Any],
    default_identities: Optional[Set[str]] = None,
) -> Optional[IdentityCapture]:
    enabled = bool(config.get("enable", False))
    if not enabled:
        return None

    save_root = config.get("root", "data/unknown")
    date_format = config.get("date_folder_format", "%m%d")
    time_format = config.get("time_format", "%H%M%S")
    extension = config.get("extension", ".jpg")
    capture_config = IdentityCaptureConfig(
        root=root / save_root,
        enabled=True,
        date_folder_format=date_format,
        time_format=time_format,
        extension=extension,
    )
    groups = _ensure_string_set(config.get("groups"))
    identities = _ensure_string_set(config.get("identities"))
    if not groups and not identities and default_identities:
        identities = set(default_identities)
    return IdentityCapture(capture_config, groups=groups, identities=identities)


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
        nose_drop=float(config.get("nose_drop")),
        neck_angle=float(config.get("neck_angle")),
    )
    return PostureService(posture_config)


def build_storage(config: Dict[str, Any]) -> Storage:
    postgres_dsn = config.get("postgres_dsn")
    if not postgres_dsn:
        raise ValueError("PostgreSQL DSN must be provided under storage.postgres_dsn")
    storage_config = StorageConfig(
        postgres_dsn=postgres_dsn,
        posture_table=config.get("table_name", "posture_events"),
        face_table=config.get("face_table_name", "face_captures"),
        reset_on_start=bool(config.get("reset_on_start", False)),
    )
    return Storage(storage_config)


def _ensure_string_set(values: Any) -> Optional[Set[str]]:
    if values is None:
        return None
    if isinstance(values, str):
        values = [values]
    parsed = {str(value).strip() for value in values or [] if str(value).strip()}
    return parsed or None


def _merge_sets(
    primary: Optional[Set[str]], secondary: Optional[Set[str]]
) -> Optional[Set[str]]:
    if not secondary:
        return primary
    if not primary:
        return set(secondary)
    return set(primary).union(secondary)


def _parse_monitoring_filters(
    settings: Dict[str, Any],
) -> Tuple[Optional[Set[str]], Optional[Set[str]]]:
    monitored_identities = _ensure_string_set(settings.get("monitored_identities"))
    monitored_groups = _ensure_string_set(settings.get("monitored_groups"))
    monitoring_block = settings.get("monitoring") or {}
    monitored_identities = _merge_sets(
        monitored_identities, _ensure_string_set(monitoring_block.get("identities"))
    )
    monitored_groups = _merge_sets(
        monitored_groups, _ensure_string_set(monitoring_block.get("groups"))
    )
    if monitored_identities is None and monitored_groups is None:
        monitored_groups = {"child"}
    return monitored_identities, monitored_groups


def make_frame_handler(
    face_service: FaceService,
    posture_service: PostureService,
    storage: Storage,
    monitored_identities: Optional[Set[str]] = None,
    monitored_groups: Optional[Set[str]] = None,
    identity_capture: Optional[IdentityCapture] = None,
    motion_gate: Optional[MotionGate] = None,
) -> Callable[[cv2.Mat], bool]:
    def handler(frame: "cv2.Mat") -> bool:
        if motion_gate is not None:
            active, reason = motion_gate.should_process()
            if not active:
                if reason:
                    logger.info("Stopping capture; {}", reason)
                return False

        matches: list[FaceMatch] = face_service.recognize(frame)
        had_faces = bool(matches)
        identity = "unknown"
        distance: Optional[float] = None
        capture_records: dict[str, Tuple[int | None, Optional[str]]] = {}
        if had_faces:
            for match in matches:
                identity_key = match.identity or "unknown"
                group = (
                    identity_key.split("/", 1)[0]
                    if "/" in identity_key
                    else identity_key or "unknown"
                )
                snapshot_path: Optional[str] = None
                if identity_capture is not None:
                    saved = identity_capture.save(identity_key, frame)
                    if saved:
                        snapshot_path = str(saved)
                face_capture_id = storage.log_face_capture(
                    identity=identity_key,
                    group_tag=group or "unknown",
                    face_distance=match.distance,
                    frame_path=snapshot_path,
                )
                capture_records[identity_key] = (face_capture_id, snapshot_path)

            primary = matches[0]
            identity = primary.identity
            distance = primary.distance
            if identity == "unknown":
                logger.info("Unknown person detected (dist {:.2f})", distance)
            else:
                logger.info("Recognized {} (dist {:.2f})", identity, distance)
            if motion_gate is not None:
                motion_gate.mark_face_seen()
        else:
            logger.debug("No faces detected in current frame; posture skipped")
            return True

        should_analyze = True
        if monitored_identities or monitored_groups:
            identity_match = (
                monitored_identities is not None and identity in monitored_identities
            )
            group_match = False
            if monitored_groups and identity not in ("", "unknown"):
                identity_group = identity.split("/", 1)[0]
                group_match = identity_group in monitored_groups
            should_analyze = identity_match or group_match
        if not should_analyze:
            logger.debug(
                "Skipping posture analysis for {}; identity not in monitored list",
                identity,
            )
            return True

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
                logger.info(
                    "Posture looks good ({:.3f} drop / {:.1f}°) for {}",
                    posture.nose_drop,
                    posture.neck_angle,
                    identity,
                )
            face_capture_id: Optional[int] = None
            capture_path: Optional[str] = None
            record = capture_records.get(identity)
            if record:
                face_capture_id, capture_path = record
            storage.log_posture(
                identity=identity,
                is_bad=posture.bad,
                nose_drop=posture.nose_drop,
                neck_angle=posture.neck_angle,
                reasons=posture.reasons,
                face_distance=distance,
                frame_path=capture_path,
                face_capture_id=face_capture_id,
            )
        else:
            logger.debug("Posture not available for {}", identity)

        return True

    return handler


def _merge_hosts(existing: str) -> set[str]:
    return {host.strip() for host in existing.split(",") if host.strip()}


def ensure_no_proxy(camera_url: str | None) -> None:
    hosts: set[str] = {"127.0.0.1", "localhost"}
    if camera_url:
        parsed = urlparse(camera_url)
        if parsed.hostname:
            hosts.add(parsed.hostname)

    for key in ("no_proxy", "NO_PROXY"):
        existing = os.environ.get(key)
        if existing:
            hosts.update(_merge_hosts(existing))

    value = ",".join(sorted(hosts))
    os.environ["no_proxy"] = value
    os.environ["NO_PROXY"] = value


def calibrate_posture(
    _posture_service: PostureService,
    _camera_url: str,
    _capture_cfg: Dict[str, Any],
    calibration_cfg: Dict[str, Any],
) -> None:
    """Placeholder hook to keep inline calibration optional."""
    if not calibration_cfg or not calibration_cfg.get("enable"):
        return
    logger.warning(
        "Inline posture calibration is not implemented. Run `scripts/calibrate_posture.sh` "
        "to generate thresholds instead."
    )


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    settings_path = root / "config" / "settings.yaml"
    settings = load_settings(settings_path)

    configure_logger(root, settings.get("logging", {}))
    logger.info("Starting StudyGuardian camera ingest")

    pir_sensor: Optional[PIRSensor] = None
    pir_cfg = settings.get("pir_sensor") or {}
    motion_gate: Optional[MotionGate] = None
    motion_event = threading.Event()
    idle_timeout_seconds = float(pir_cfg.get("no_face_timeout_seconds", 10.0))

    def _on_motion(active: bool) -> None:
        nonlocal motion_gate
        if not active or motion_gate is None:
            return
        motion_gate.activate()
        motion_event.set()
        logger.info(
            "PIR motion detected; capture running until {}s of no faces",
            idle_timeout_seconds,
        )

    try:
        motion_gate = MotionGate(idle_timeout_seconds)
        pir_sensor = build_pir_sensor(pir_cfg, on_motion=_on_motion)
        if pir_sensor:
            logger.info(
                "PIR sensor enabled (no_face_timeout_seconds={:.0f})",
                idle_timeout_seconds,
            )
    except Exception as exc:  # pragma: no cover - hardware/runtime concerns
        logger.warning("PIR sensor failed to start: {}", exc)

    posture_cfg = settings.get("posture", {}) or {}
    if posture_cfg.get("nose_drop") is None or posture_cfg.get("neck_angle") is None:
        raise RuntimeError(
            "Posture thresholds not configured. Run `python scripts/calibrate_posture.py` "
            "to set posture.nose_drop and posture.neck_angle in config/settings.yaml"
        )

    face_service = build_face_service(root, settings.get("face_recognition", {}))
    posture_service = build_posture_service(posture_cfg)
    storage = build_storage(settings.get("storage", {}))
    monitored_identities, monitored_groups = _parse_monitoring_filters(settings)
    face_capture_cfg = settings.get("face_capture")
    default_identities: Optional[Set[str]] = None
    if face_capture_cfg is None:
        face_capture_cfg = settings.get("unknown_capture", {})
        default_identities = {"unknown"}
    face_capture_cfg = face_capture_cfg or {}
    identity_capture = build_identity_capture(
        root, face_capture_cfg, default_identities=default_identities
    )

    capture_cfg = settings.get("capture", {})
    ensure_no_proxy(settings.get("camera_url"))

    calibrate_posture(
        posture_service,
        settings.get("camera_url", ""),
        capture_cfg,
        settings.get("posture_calibration", {}),
    )

    def _iterate_stream() -> None:
        stream = CameraStream(
            source=settings.get("camera_url", ""),
            target_fps=float(capture_cfg.get("target_fps", 15)),
            reconnect_delay=float(capture_cfg.get("reconnect_delay", 5)),
            max_retries=int(capture_cfg.get("max_retries", 3)),
            frame_saver=None,
        )
        try:
            stream.iterate(
                on_frame=make_frame_handler(
                    face_service,
                    posture_service,
                    storage,
                    monitored_identities=monitored_identities,
                    monitored_groups=monitored_groups,
                    identity_capture=identity_capture,
                    motion_gate=motion_gate if pir_sensor else None,
                )
            )
        except Exception as exc:  # pragma: no cover - runtime concerns
            logger.warning("Stream iteration failed: {}", exc)
            if motion_gate:
                motion_gate.deactivate()
        finally:
            stream.release()

    try:
        while True:
            if pir_sensor:
                logger.info("Waiting for PIR motion to start capture")
                try:
                    motion_event.wait()
                except KeyboardInterrupt:
                    logger.info(
                        "Interrupted while waiting for PIR motion, shutting down"
                    )
                    break
                motion_event.clear()
            _iterate_stream()
            if not pir_sensor:
                break
    except KeyboardInterrupt:
        logger.info("Interrupted, shutting down")
    except Exception as exc:  # pragma: no cover - runtime concerns
        logger.warning("Stream ingestion failed: {}", exc)
    finally:
        storage.close()
        posture_service.close()
        if pir_sensor:
            pir_sensor.close()


if __name__ == "__main__":
    main()
