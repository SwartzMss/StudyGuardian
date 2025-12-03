"""StudyGuardian entry point for camera stream ingestion."""

from __future__ import annotations

import os
import sys
import threading
import time
from urllib.parse import urlparse
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Set, Tuple
import shutil

import cv2
import yaml
from loguru import logger

# Quiet TFLite / cpuinfo warnings unless user overrides.
os.environ.setdefault("CPUINFO_LOG_LEVEL", "error")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "1")

from agent.capture import (
    CameraStream,
    ensure_camera_settings,
    FrameSaveConfig,
    FrameSaver,
    IdentityCapture,
    IdentityCaptureConfig,
)
from agent.posture import PostureConfig, PostureService
from agent.recognition import FaceMatch, FaceService
from agent.storage import (
    Storage,
    StorageConfig,
    FaceCaptureRetentionWorker,
)
from agent.sensors import (
    Buzzer,
    DHT22Sensor,
    PIRSensor,
    build_buzzer,
    build_dht22_sensor,
    build_pir_sensor,
)


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


def reset_capture_directory(root: Path, settings: Dict[str, Any]) -> None:
    storage_cfg = settings.get("storage") or {}
    if not storage_cfg.get("reset_on_start"):
        return

    capture_cfg = settings.get("face_capture") or settings.get("unknown_capture") or {}
    capture_root = capture_cfg.get("root", "data/captures")
    capture_path = (root / capture_root).resolve()
    is_within_project = capture_path == root or root in capture_path.parents
    if not is_within_project:
        logger.warning(
            "reset_on_start set but capture path is outside project, skipping: {}",
            capture_path,
        )
        return

    if capture_path.exists():
        try:
            shutil.rmtree(capture_path)
            logger.info(
                "reset_on_start enabled; removed capture directory {}",
                capture_path,
            )
        except Exception as exc:  # pragma: no cover - filesystem/runtime concerns
            logger.warning("Failed to remove capture directory {}: {}", capture_path, exc)


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
    min_face_area_ratio_raw = config.get("min_face_area_ratio")
    min_face_area_ratio = (
        float(min_face_area_ratio_raw)
        if min_face_area_ratio_raw is not None
        else None
    )
    service = FaceService.from_known_directory(
        root / known_dir,
        tolerance=tolerance,
        location_model=location_model,
        min_face_area_ratio=min_face_area_ratio,
    )
    return service


def build_posture_service(config: Dict[str, Any]) -> PostureService:
    neck_raw = config.get("neck_angle")
    neck_angle = float(neck_raw) if neck_raw is not None else None
    vis_raw = config.get("visibility_threshold")
    visibility_threshold = float(vis_raw) if vis_raw is not None else None
    posture_config = PostureConfig(
        nose_drop=float(config.get("nose_drop")),
        neck_angle=neck_angle,
        visibility_threshold=visibility_threshold,
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


def _derive_allowed_groups(settings: Dict[str, Any]) -> Optional[Set[str]]:
    """Use face_capture groups to decide whose posture to check."""
    capture_cfg = settings.get("face_capture") or {}
    return _ensure_string_set(capture_cfg.get("groups"))


def make_frame_handler(
    face_service: FaceService,
    posture_service: PostureService,
    storage: Storage,
    allowed_groups: Optional[Set[str]] = None,
    allowed_group_grace_seconds: float = 5.0,
    identity_capture: Optional[IdentityCapture] = None,
    motion_gate: Optional[MotionGate] = None,
    buzzer: Optional[Buzzer] = None,
    buzzer_beep_count: int = 2,
    buzzer_beep_interval: float = 0.4,
    buzzer_min_gap_seconds: float = 5.0,
) -> Callable[[cv2.Mat], bool]:
    last_beep_ts = 0.0
    last_allowed_seen_ts = 0.0
    last_allowed_identity = "unknown"

    def handler(frame: "cv2.Mat") -> bool:
        nonlocal last_beep_ts
        nonlocal last_allowed_seen_ts
        nonlocal last_allowed_identity
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
        capture_records: dict[str, Tuple[str | None, Optional[str]]] = {}
        now = time.monotonic()
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

                if allowed_groups and group in allowed_groups:
                    last_allowed_seen_ts = now
                    last_allowed_identity = identity_key

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
            logger.debug("No faces detected in current frame")

        allowed_window_active = False
        if allowed_groups:
            identity_group = None
            if identity not in ("", "unknown"):
                identity_group = identity.split("/", 1)[0]
            allowed_window_active = (
                last_allowed_seen_ts > 0
                and (now - last_allowed_seen_ts) <= allowed_group_grace_seconds
            )
            current_allowed = identity_group in allowed_groups if identity_group else False
            if current_allowed:
                last_allowed_seen_ts = now
                last_allowed_identity = identity or last_allowed_identity
                allowed_window_active = True  # current frame is allowed, keep window active
            if not current_allowed and not allowed_window_active:
                logger.debug(
                    "Skipping posture analysis for {}; group %s not allowed",
                    identity,
                    identity_group or "unknown",
                )
                return True

        # If in allowed window but current frame has no faces/unknown, reuse last allowed identity.
        if allowed_groups and allowed_window_active and identity in ("", "unknown"):
            identity = last_allowed_identity or "unknown"

        # Without allowed_groups, analyze everyone. With allowed_groups, analyze if current group is allowed or window is active.

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
            elif identity_capture is not None:
                saved = identity_capture.save(identity, frame)
                if saved:
                    capture_path = str(saved)
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
            if posture.bad and buzzer is not None:
                now = time.monotonic()
                if (now - last_beep_ts) >= buzzer_min_gap_seconds:
                    try:
                        buzzer.beep_times(buzzer_beep_count, interval=buzzer_beep_interval)
                    except Exception as exc:  # pragma: no cover - hardware/runtime concerns
                        logger.warning("Buzzer failed to beep: {}", exc)
                    finally:
                        last_beep_ts = now
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


class EnvLogger:
    """Minimal logger for environment readings."""

    def __init__(
        self,
        dsn: str,
        table: str = "environment_events",
        retention_days: Optional[float] = 3.0,
    ) -> None:
        try:
            import psycopg2  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "psycopg2-binary is required for environment logging"
            ) from exc

        self._table = table
        self._conn = psycopg2.connect(dsn)
        self._retention_days = (
            float(retention_days) if retention_days and retention_days > 0 else None
        )
        self._ensure_table()

    def _ensure_table(self) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
              id BIGSERIAL PRIMARY KEY,
              temperature DOUBLE PRECISION,
              humidity DOUBLE PRECISION,
              timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cursor.close()
        self._conn.commit()

    def log(self, humidity: float, temperature: float) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            f"INSERT INTO {self._table} (humidity, temperature) VALUES (%s, %s)",
            (humidity, temperature),
        )
        if self._retention_days:
            cursor.execute(
                f"DELETE FROM {self._table} "
                "WHERE timestamp < (NOW() - (%s || ' days')::interval)",
                (self._retention_days,),
            )
        cursor.close()
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    settings_path = root / "config" / "settings.yaml"
    settings = load_settings(settings_path)

    configure_logger(root, settings.get("logging", {}))
    logger.info("Starting StudyGuardian camera ingest")
    reset_capture_directory(root, settings)

    buzzer: Optional[Buzzer] = None
    buzzer_cfg = settings.get("buzzer") or {}
    buzzer_beep_count = int(buzzer_cfg.get("beep_count", 2))
    buzzer_beep_interval = float(buzzer_cfg.get("beep_interval_seconds", 0.4))
    buzzer_min_gap_seconds = float(buzzer_cfg.get("min_gap_seconds", 5.0))

    dht_sensor: Optional[DHT22Sensor] = None
    dht_thread: Optional[threading.Thread] = None
    dht_stop_event = threading.Event()
    dht_cfg = settings.get("dht22") or {}
    dht_interval = max(1.0, float(dht_cfg.get("poll_interval_seconds", 30.0)))
    dht_retention_days_raw = dht_cfg.get("retention_days", 3.0)
    dht_retention_days = (
        float(dht_retention_days_raw) if dht_retention_days_raw is not None else None
    )
    env_logger: Optional[EnvLogger] = None
    env_table = "environment_events"
    env_dsn = (settings.get("storage") or {}).get("postgres_dsn")

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

    def _poll_dht() -> None:
        while not dht_stop_event.is_set():
            if dht_sensor is None:
                break
            humidity, temperature = dht_sensor.read()
            if humidity is None or temperature is None:
                logger.debug("DHT22 reading unavailable")
            else:
                logger.info(
                    "DHT22 {:.1f}% RH / {:.1f}°C", humidity, temperature
                )
                if env_logger:
                    try:
                        env_logger.log(humidity, temperature)
                    except Exception as exc:
                        logger.debug("DHT22 DB log failed: {}", exc)
            dht_stop_event.wait(dht_interval)

    try:
        motion_gate = MotionGate(idle_timeout_seconds)
        pir_sensor = build_pir_sensor(pir_cfg, on_motion=_on_motion)
        buzzer = build_buzzer(buzzer_cfg)
        dht_sensor = build_dht22_sensor(dht_cfg)
        if dht_sensor and env_dsn:
            try:
                env_logger = EnvLogger(
                    env_dsn,
                    table=env_table,
                    retention_days=dht_retention_days,
                )
                logger.info(
                    "Env logging enabled to table {} (poll every {:.0f}s, retention={}d)",
                    env_table,
                    dht_interval,
                    dht_retention_days if dht_retention_days is not None else "∞",
                )
            except Exception as exc:
                logger.warning("Env logger not started: {}", exc)
        if pir_sensor:
            logger.info(
                "PIR sensor enabled (no_face_timeout_seconds={:.0f})",
                idle_timeout_seconds,
            )
        if buzzer:
            logger.info(
                "Buzzer ready (beep_count={}, min_gap={:.1f}s)",
                buzzer_beep_count,
                buzzer_min_gap_seconds,
            )
        if dht_sensor:
            dht_thread = threading.Thread(target=_poll_dht, daemon=True)
            dht_thread.start()
            logger.info(
                "DHT22 sensor enabled on BCM {} (poll every {:.0f}s)",
                dht_sensor.pin,
                dht_interval,
            )
    except Exception as exc:  # pragma: no cover - hardware/runtime concerns
        logger.warning("Sensor initialization failed: {}", exc)

    posture_cfg = settings.get("posture", {}) or {}
    if posture_cfg.get("nose_drop") is None:
        raise RuntimeError(
            "Posture thresholds not configured. Run `python scripts/calibrate_posture.py` "
            "to set posture.nose_drop in config/settings.yaml (neck_angle 可设为 null 禁用颈部检测)"
        )

    face_service = build_face_service(root, settings.get("face_recognition", {}))
    posture_service = build_posture_service(posture_cfg)
    storage = build_storage(settings.get("storage", {}))
    retention_worker: Optional[FaceCaptureRetentionWorker] = None
    retention_cfg = settings.get("face_capture_retention", {}) or {}
    if retention_cfg:
        max_rows = retention_cfg.get("max_rows")
        max_age_days = retention_cfg.get("max_age_days")
        interval = float(retention_cfg.get("interval_seconds", 600))
        retention_worker = FaceCaptureRetentionWorker(
            storage=storage,
            max_rows=int(max_rows) if max_rows is not None else None,
            max_age_days=float(max_age_days) if max_age_days is not None else None,
            interval_seconds=interval,
        )
        retention_worker.start()
    allowed_groups = _derive_allowed_groups(settings)
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
        ensure_camera_settings(settings.get("camera_url", ""))
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
                    allowed_groups=allowed_groups,
                    allowed_group_grace_seconds=float(
                        capture_cfg.get("allowed_group_grace_seconds", 5.0)
                    ),
                    identity_capture=identity_capture,
                    motion_gate=motion_gate if pir_sensor else None,
                    buzzer=buzzer,
                    buzzer_beep_count=buzzer_beep_count,
                    buzzer_beep_interval=buzzer_beep_interval,
                    buzzer_min_gap_seconds=buzzer_min_gap_seconds,
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
        if retention_worker:
            retention_worker.stop()
        if env_logger:
            env_logger.close()
        if dht_sensor:
            dht_stop_event.set()
            if dht_thread:
                dht_thread.join(timeout=2)
            dht_sensor.close()
        if pir_sensor:
            pir_sensor.close()
        if buzzer:
            buzzer.close()


if __name__ == "__main__":
    main()
