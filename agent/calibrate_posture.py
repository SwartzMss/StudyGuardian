"""Calibrate posture thresholds based on a short capture session.

Usage:
    python agent/calibrate_posture.py --samples 30 --nose-margin 0.03 --angle-margin 5
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path
from typing import Any, Dict, List
import sys

import cv2
import mediapipe as mp
import yaml
from loguru import logger

# Ensure project root is importable when running as a script
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.capture import CameraStream
from agent.main import build_posture_service, ensure_no_proxy, load_settings

_DRAWING_UTILS = mp.solutions.drawing_utils
_DRAWING_STYLES = mp.solutions.drawing_styles


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate posture thresholds and update settings.yaml")
    parser.add_argument("--settings", type=Path, default=Path("config/settings.yaml"), help="Path to settings.yaml")
    parser.add_argument("--samples", type=int, default=30, help="Target number of valid samples")
    parser.add_argument(
        "--max-frames", type=int, default=None, help="Optional frame cap (defaults to samples * 2 if omitted)"
    )
    parser.add_argument("--nose-margin", type=float, default=0.03, help="Extra margin added to avg nose_drop")
    parser.add_argument("--angle-margin", type=float, default=5.0, help="Extra margin added to avg neck_angle")
    parser.add_argument(
        "--save-dir", type=Path, default=Path("data/calibration"), help="Directory to save annotated snapshots"
    )
    return parser.parse_args()


def _collect_samples(
    camera_url: str,
    capture_cfg: Dict[str, Any],
    target_samples: int,
    max_frames: int,
    posture_service,
    save_dir: Path,
) -> tuple[List[float], List[float]]:
    drops: list[float] = []
    angles: list[float] = []

    save_dir.mkdir(parents=True, exist_ok=True)

    stream = CameraStream(
        source=camera_url,
        target_fps=float(capture_cfg.get("target_fps", 15)),
        reconnect_delay=float(capture_cfg.get("reconnect_delay", 5)),
        max_retries=int(capture_cfg.get("max_retries", 3)),
        frame_saver=None,
    )

    def _collect(frame: "Any") -> bool:
        assessment, landmarks = posture_service.analyze_with_landmarks(frame)
        if assessment:
            drops.append(assessment.nose_drop)
            angles.append(assessment.neck_angle)
            _save_snapshot(
                frame,
                landmarks,
                save_dir,
                sample_idx=len(drops),
                nose_drop=assessment.nose_drop,
                neck_angle=assessment.neck_angle,
            )
            logger.info(
                "Sample #%d nose_drop=%.3f neck_angle=%.1f",
                len(drops),
                assessment.nose_drop,
                assessment.neck_angle,
            )
        if len(drops) >= target_samples:
            return False
        return True

    try:
        stream.iterate(on_frame=_collect, max_frames=max_frames)
    finally:
        stream.release()

    return drops, angles


def main() -> None:
    args = parse_args()
    settings_path = args.settings
    root = Path(__file__).resolve().parents[1]

    settings = load_settings(root / settings_path)
    camera_url = settings.get("camera_url", "")
    ensure_no_proxy(camera_url)
    capture_cfg = settings.get("capture", {}) or {}
    posture_cfg = settings.get("posture", {}) or {}
    save_dir = args.save_dir if args.save_dir.is_absolute() else (root / args.save_dir)

    target_samples = args.samples
    max_frames = args.max_frames or target_samples * 2

    logger.info(
        "Starting posture calibration: samples=%d max_frames=%d nose_margin=%.3f angle_margin=%.1f",
        target_samples,
        max_frames,
        args.nose_margin,
        args.angle_margin,
    )

    posture_service = build_posture_service(posture_cfg)
    drops, angles = _collect_samples(camera_url, capture_cfg, target_samples, max_frames, posture_service, save_dir)
    posture_service.close()

    if not drops:
        raise RuntimeError("No valid posture samples collected; ensure subject is in frame with shoulders visible")

    avg_drop = sum(drops) / len(drops)
    avg_angle = sum(angles) / len(angles)
    new_drop = avg_drop + args.nose_margin
    new_angle = avg_angle + args.angle_margin

    settings.setdefault("posture", {})
    settings["posture"]["nose_drop"] = round(new_drop, 4)
    settings["posture"]["neck_angle"] = round(new_angle, 2)
    settings["posture_metadata"] = {
        "calibrated_at": dt.datetime.utcnow().isoformat() + "Z",
        "samples": len(drops),
        "nose_margin": args.nose_margin,
        "angle_margin": args.angle_margin,
        "avg_drop": round(avg_drop, 4),
        "avg_angle": round(avg_angle, 2),
    }

    with (root / settings_path).open("w", encoding="utf-8") as handle:
        yaml.safe_dump(settings, handle, sort_keys=False, allow_unicode=True)

    logger.info(
        "Calibration complete. Updated thresholds: nose_drop=%.4f neck_angle=%.2f (avg_drop=%.4f avg_angle=%.2f)",
        new_drop,
        new_angle,
        avg_drop,
        avg_angle,
    )
    logger.info("Settings saved to %s", (root / settings_path))


def _save_snapshot(
    frame: "Any",
    landmarks: Any,
    save_dir: Path,
    sample_idx: int,
    nose_drop: float,
    neck_angle: float,
) -> None:
    annotated = frame.copy()
    timestamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if landmarks is not None:
        _DRAWING_UTILS.draw_landmarks(
            annotated,
            landmarks,
            mp.solutions.pose.POSE_CONNECTIONS,
            landmark_drawing_spec=_DRAWING_STYLES.get_default_pose_landmarks_style(),
        )
    overlay = f"Sample {sample_idx}  nose_drop={nose_drop:.3f}  neck_angle={neck_angle:.1f}  {timestamp}"
    cv2.putText(
        annotated,
        overlay,
        (10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 255, 0),
        2,
        cv2.LINE_AA,
    )
    filename = f"calibration_{sample_idx:03d}.jpg"
    path = save_dir / filename
    cv2.imwrite(str(path), annotated)


if __name__ == "__main__":
    main()
