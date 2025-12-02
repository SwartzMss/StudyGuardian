"""Live posture test: open camera, assess posture, and beep on bad frames."""

from __future__ import annotations

import argparse
import signal
from pathlib import Path
from typing import Any, Dict

from loguru import logger

from agent.capture import CameraStream, ensure_camera_settings
from agent.main import build_posture_service, ensure_no_proxy, load_settings
from agent.sensors import build_buzzer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run live posture detection and beep on bad posture"
    )
    parser.add_argument(
        "--settings",
        type=Path,
        default=Path("config/settings.yaml"),
        help="Path to settings.yaml",
    )
    parser.add_argument(
        "--camera-url",
        type=str,
        default=None,
        help="Override camera URL (defaults to settings.camera_url)",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="Optional cap on frames to process (Ctrl+C to stop otherwise)",
    )
    parser.add_argument(
        "--beep-count",
        type=int,
        default=None,
        help="Override beep_count from settings.buzzer",
    )
    parser.add_argument(
        "--beep-interval",
        type=float,
        default=None,
        help="Override beep_interval_seconds from settings.buzzer",
    )
    return parser.parse_args()


def _load_config(settings_path: Path) -> Dict[str, Any]:
    if not settings_path.exists():
        raise FileNotFoundError(f"Missing configuration at {settings_path}")
    return load_settings(settings_path)


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    settings = _load_config(root / args.settings)

    camera_url = args.camera_url or settings.get("camera_url", "")
    if not camera_url:
        raise RuntimeError("camera_url is required (set in settings or pass --camera-url)")

    ensure_no_proxy(camera_url)
    capture_cfg = settings.get("capture", {}) or {}
    posture_cfg = settings.get("posture", {}) or {}
    buzzer_cfg = settings.get("buzzer", {}) or {}

    beep_count = int(args.beep_count) if args.beep_count is not None else int(
        buzzer_cfg.get("beep_count", 2)
    )
    beep_interval = float(args.beep_interval) if args.beep_interval is not None else float(
        buzzer_cfg.get("beep_interval_seconds", 0.4)
    )

    ensure_camera_settings(camera_url)

    posture_service = build_posture_service(posture_cfg)
    buzzer = build_buzzer(buzzer_cfg)

    stream = CameraStream(
        source=camera_url,
        target_fps=float(capture_cfg.get("target_fps", 15)),
        reconnect_delay=float(capture_cfg.get("reconnect_delay", 5)),
        max_retries=int(capture_cfg.get("max_retries", 3)),
        frame_saver=None,
    )

    stop = False

    def _handle_sigint(_sig: int, _frame: Any) -> None:
        nonlocal stop
        stop = True
        logger.info("Stopping live posture test...")

    signal.signal(signal.SIGINT, _handle_sigint)

    def _on_frame(frame: "Any") -> bool:
        nonlocal stop
        if stop:
            return False

        assessment = posture_service.analyze(frame)
        if assessment is None:
            logger.debug("No posture assessment for current frame")
            return True

        if assessment.bad:
            logger.warning(
                "Bad posture detected: drop={:.3f} neck={:.1f} reasons={}",
                assessment.nose_drop,
                assessment.neck_angle,
                ", ".join(assessment.reasons),
            )
            if buzzer is not None:
                try:
                    buzzer.beep_times(beep_count, interval=beep_interval)
                except Exception as exc:  # pragma: no cover - hardware/runtime concerns
                    logger.warning("Buzzer failed: {}", exc)
        else:
            logger.info(
                "Posture OK: drop={:.3f} neck={:.1f}",
                assessment.nose_drop,
                assessment.neck_angle,
            )

        return not stop

    try:
        logger.info("Starting live posture test (Ctrl+C to stop)")
        stream.iterate(on_frame=_on_frame, max_frames=args.max_frames)
    finally:
        stream.release()
        posture_service.close()
        if buzzer:
            buzzer.close()


if __name__ == "__main__":
    main()
