"""Posture detection helpers for StudyGuardian agent."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

import cv2
import mediapipe as mp
import numpy as np
from loguru import logger

from mediapipe.framework.formats import landmark_pb2


@dataclass
class PostureConfig:
    nose_drop: float = 0.12
    neck_angle: float = 45.0


@dataclass
class PostureAssessment:
    bad: bool
    nose_drop: float
    neck_angle: float
    reasons: List[str]


class PostureService:
    """Wraps MediaPipe Pose to derive simple posture assessments."""

    def __init__(self, config: PostureConfig) -> None:
        self._config = config
        self._mp_pose = mp.solutions.pose
        self._pose = self._mp_pose.Pose(min_detection_confidence=0.4, min_tracking_confidence=0.4)

    def analyze(self, frame: "np.ndarray") -> Optional[PostureAssessment]:
        if frame is None:
            return None

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self._pose.process(rgb)
        landmarks = results.pose_landmarks
        if landmarks is None or not landmarks.landmark:
            logger.debug("No pose landmarks detected")
            return None

        lm = landmarks.landmark
        nose = lm[self._mp_pose.PoseLandmark.NOSE]
        left_shoulder = lm[self._mp_pose.PoseLandmark.LEFT_SHOULDER]
        right_shoulder = lm[self._mp_pose.PoseLandmark.RIGHT_SHOULDER]
        left_hip = lm[self._mp_pose.PoseLandmark.LEFT_HIP]
        right_hip = lm[self._mp_pose.PoseLandmark.RIGHT_HIP]

        shoulder_center = self._average_point((left_shoulder, right_shoulder))
        hip_center = self._average_point((left_hip, right_hip))

        nose_drop = nose.y - shoulder_center.y
        neck_angle = self._angle_between(nose, shoulder_center, hip_center)

        reasons: List[str] = []
        if nose_drop > self._config.nose_drop:
            reasons.append("head lowered")
        if neck_angle > self._config.neck_angle:
            reasons.append("neck extended")

        bad = bool(reasons)
        return PostureAssessment(bad=bad, nose_drop=nose_drop, neck_angle=neck_angle, reasons=reasons)

    @staticmethod
    def _average_point(points: Sequence[landmark_pb2.NormalizedLandmark]) -> landmark_pb2.NormalizedLandmark:
        x = sum(point.x for point in points) / len(points)
        y = sum(point.y for point in points) / len(points)
        z = sum(point.z for point in points) / len(points)
        return landmark_pb2.NormalizedLandmark(x=x, y=y, z=z)

    @staticmethod
    def _angle_between(
        a: landmark_pb2.NormalizedLandmark,
        b: landmark_pb2.NormalizedLandmark,
        c: landmark_pb2.NormalizedLandmark,
    ) -> float:
        ba = np.array([a.x - b.x, a.y - b.y])
        bc = np.array([c.x - b.x, c.y - b.y])
        dot = np.dot(ba, bc)
        denom = np.linalg.norm(ba) * np.linalg.norm(bc)
        if denom == 0:
            return 0.0
        cos_angle = max(min(dot / denom, 1.0), -1.0)
        return math.degrees(math.acos(cos_angle))

    def close(self) -> None:
        self._pose.close()
