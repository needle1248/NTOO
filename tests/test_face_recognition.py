import logging

import cv2
import numpy as np

from app.config import Settings
from app.services.face_recognition import FaceRecognitionService


class _BrokenDetector:
    def empty(self) -> bool:
        return False

    def detectMultiScale(self, *args, **kwargs):
        raise cv2.error("detectMultiScale", "", "boom", -1)


def test_detect_face_returns_none_on_opencv_error(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        faces_dir=tmp_path / "faces",
        models_dir=tmp_path / "models",
        snapshot_dir=tmp_path / "snapshots",
        face_backend="simple_pca",
    )
    service = FaceRecognitionService(settings, logging.getLogger("test-face"))
    service.detector = _BrokenDetector()

    gray = np.zeros((240, 320), dtype=np.uint8)

    assert service._detect_face(gray, relax=True) is None
