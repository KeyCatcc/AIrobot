"""Face-detection backends for the conservative head-follow feature.

The default backend is OpenCV's YuNet DNN detector.  Haar remains available
only as an explicit diagnostic fallback because it was too intermittent on
the robot camera for reliable visitor following.
"""

from __future__ import annotations

from pathlib import Path

import cv2


class FaceDetector:
    """Small common interface returning ``(x1, y1, x2, y2, confidence)``."""

    def detect(self, frame):  # pragma: no cover - interface only
        raise NotImplementedError


class YuNetFaceDetector(FaceDetector):
    """OpenCV DNN YuNet face detector, suitable for CPU-only ARM systems."""

    def __init__(
        self,
        model_path: Path,
        *,
        score_threshold: float = 0.75,
        nms_threshold: float = 0.30,
        top_k: int = 5000,
    ) -> None:
        if not hasattr(cv2, "FaceDetectorYN"):
            raise RuntimeError("This OpenCV build has no FaceDetectorYN / YuNet support")
        if not model_path.is_file():
            raise RuntimeError(
                "YuNet model is missing: %s. Install the declared model file before "
                "running face follow." % model_path
            )
        self.detector = cv2.FaceDetectorYN.create(
            str(model_path), "", (320, 320), score_threshold, nms_threshold, top_k
        )

    def detect(self, frame):
        height, width = frame.shape[:2]
        self.detector.setInputSize((width, height))
        _status, detections = self.detector.detect(frame)
        if detections is None:
            return []
        result = []
        for row in detections:
            x, y, box_width, box_height = (float(value) for value in row[:4])
            confidence = float(row[-1])
            result.append((x, y, x + box_width, y + box_height, confidence))
        return result


class HaarFaceDetector(FaceDetector):
    """Diagnostic-only fallback retained to compare camera/detector behavior."""

    def __init__(self, *, min_neighbors: int, min_face_size: int) -> None:
        cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        self.detector = cv2.CascadeClassifier(str(cascade_path))
        if self.detector.empty():
            raise RuntimeError(f"Cannot load Haar cascade: {cascade_path}")
        self.min_neighbors = min_neighbors
        self.min_face_size = min_face_size

    def detect(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        raw = self.detector.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=self.min_neighbors,
            minSize=(self.min_face_size, self.min_face_size),
        )
        return [
            (float(x), float(y), float(x + width), float(y + height), 0.90)
            for x, y, width, height in raw
        ]


def build_face_detector(
    backend: str,
    *,
    model_path: Path,
    score_threshold: float,
    min_neighbors: int,
    min_face_size: int,
) -> FaceDetector:
    if backend == "yunet":
        return YuNetFaceDetector(model_path, score_threshold=score_threshold)
    if backend == "haar":
        return HaarFaceDetector(min_neighbors=min_neighbors, min_face_size=min_face_size)
    raise ValueError(f"Unknown detector backend: {backend}")
