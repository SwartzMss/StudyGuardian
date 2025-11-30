"""Capture helpers grouped under agent.capture."""

from .ingest import (
    CameraStream,
    ensure_camera_settings,
    FrameSaveConfig,
    FrameSaver,
    IdentityCapture,
    IdentityCaptureConfig,
)

__all__ = [
    "CameraStream",
    "ensure_camera_settings",
    "FrameSaveConfig",
    "FrameSaver",
    "IdentityCapture",
    "IdentityCaptureConfig",
]
