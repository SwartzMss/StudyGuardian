"""Face recognition helpers used by the StudyGuardian agent."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

import cv2
import face_recognition
import numpy as np
from loguru import logger


@dataclass
class FaceMatch:
    identity: str
    distance: float
    location: Tuple[int, int, int, int]


def _hash_path(path: Path) -> str:
    return hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:8]


class FaceService:
    """Load known faces and match incoming frames against them."""

    def __init__(
        self,
        encodings: Sequence[np.ndarray],
        labels: Sequence[str],
        tolerance: float = 0.55,
        location_model: str = "hog",
    ) -> None:
        self._encodings = list(encodings)
        self._labels = list(labels)
        self._tolerance = tolerance
        self._location_model = location_model

    @classmethod
    def from_known_directory(
        cls, base_dir: Path, tolerance: float = 0.55, location_model: str = "hog"
    ) -> "FaceService":
        base_dir = base_dir.resolve()
        if not base_dir.exists():
            logger.warning("Known face directory %s does not exist, no identities loaded", base_dir)
            return cls([], [], tolerance)

        encodings: List[np.ndarray] = []
        labels: List[str] = []

        for person_dir in sorted(base_dir.iterdir()):
            if not person_dir.is_dir():
                continue
            identity = person_dir.name
            for image_path in sorted(person_dir.glob("*")):
                if not image_path.is_file():
                    continue
                image = face_recognition.load_image_file(str(image_path))
                faces = face_recognition.face_encodings(image)
                if not faces:
                    logger.warning("No face detected in %s, skipping", image_path)
                    continue
                encodings.append(faces[0])
                labels.append(identity)
                logger.debug(
                    "Loaded %s (%s) with hash %s", identity, image_path.name, _hash_path(image_path)
                )

        if not encodings:
            logger.warning("No known faces loaded from %s", base_dir)

        return cls(encodings, labels, tolerance, location_model)

    def recognize(self, frame: "np.ndarray") -> List[FaceMatch]:
        if frame is None:
            return []

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        locations = face_recognition.face_locations(rgb, model=self._location_model)
        encodings = face_recognition.face_encodings(rgb, locations)
        matches: List[FaceMatch] = []

        for location, encoding in zip(locations, encodings):
            if not self._encodings:
                matches.append(FaceMatch("unknown", 1.0, location))
                continue

            distances = face_recognition.face_distance(self._encodings, encoding)
            best_idx = int(np.argmin(distances))
            best_distance = float(distances[best_idx])
            identity = self._labels[best_idx] if best_distance <= self._tolerance else "unknown"
            matches.append(FaceMatch(identity, best_distance, location))

        return matches
