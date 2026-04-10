from __future__ import annotations

import logging
import time
from collections import deque
from pathlib import Path
from typing import Any

from app.config import Settings
from app.models import CameraSnapshot

try:
    from app.services.face_recognition import FaceRecognitionService
except ModuleNotFoundError as exc:
    FaceRecognitionService = None  # type: ignore[assignment]
    FACE_IMPORT_ERROR: ModuleNotFoundError | None = exc
else:
    FACE_IMPORT_ERROR = None


class FaceRuntimeService:
    """Keeps the imported face-recognition code isolated from board I/O."""

    def __init__(self, settings: Settings, logger: logging.Logger | None = None) -> None:
        self.settings = settings
        self.logger = logger or logging.getLogger("face_runtime")
        self._ensure_dirs()
        self.face_service = (
            FaceRecognitionService(settings, self.logger)
            if FaceRecognitionService is not None
            else None
        )
        self.snapshots: deque[CameraSnapshot] = deque(maxlen=settings.face_max_snapshots)

    def startup(self) -> None:
        if self.face_service is None:
            self.logger.warning("Face recognition is unavailable: %s", FACE_IMPORT_ERROR)
            return
        try:
            self.face_service.load_if_available()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Face model was not loaded: %s", exc)

    def get_status(self) -> dict[str, Any]:
        if self.face_service is None:
            return {
                "loaded": False,
                "backend": self.settings.face_backend,
                "known_users": [],
                "threshold": self.settings.face_match_threshold,
                "error": str(FACE_IMPORT_ERROR),
                "snapshots": [],
            }
        try:
            status = self.face_service.get_status()
        except Exception as exc:  # noqa: BLE001
            status = {
                "loaded": False,
                "backend": self.settings.face_backend,
                "known_users": [],
                "threshold": self.settings.face_match_threshold,
                "error": str(exc),
            }
        return {
            **status,
            "snapshots": [snapshot.model_dump(mode="json") for snapshot in self.get_recent_snapshots()],
        }

    def get_recent_snapshots(self, limit: int | None = None) -> list[CameraSnapshot]:
        items = list(self.snapshots)
        if limit is not None:
            items = items[-limit:]
        return list(reversed(items))

    def train_faces_from_uploads(self, user_id: str, files: list[tuple[str, bytes]]) -> dict[str, Any]:
        self._require_face_service()
        return self.face_service.register_user_images(user_id, files)

    def retrain_faces(self) -> dict[str, Any]:
        self._require_face_service()
        return self.face_service.retrain_from_disk()

    def recognize_face_bytes(self, device_id: int, image_bytes: bytes) -> dict[str, Any]:
        self._require_face_service()
        candidates = self._recognition_candidates(image_bytes)
        prediction = None
        selected_image_bytes = candidates[0][1]
        selected_orientation = candidates[0][0]

        for orientation, candidate_bytes in candidates:
            candidate_prediction = self.face_service.predict(candidate_bytes, device_id=device_id)
            if prediction is None or candidate_prediction.confidence > prediction.confidence:
                prediction = candidate_prediction
                selected_image_bytes = candidate_bytes
                selected_orientation = orientation
            if candidate_prediction.matched:
                break

        assert prediction is not None
        snapshot = self._store_snapshot(device_id, selected_image_bytes)

        snapshot.matched = prediction.matched
        snapshot.user_id = prediction.user_id
        snapshot.confidence = prediction.confidence

        return {
            "prediction": prediction.model_dump(mode="json"),
            "snapshot_path": str(Path(self.settings.snapshot_dir) / snapshot.filename),
            "snapshot_url": snapshot.image_url,
            "orientation": selected_orientation,
        }

    def enroll_face_bytes(
        self,
        user_id: str,
        device_id: int,
        image_bytes: bytes,
        retrain: bool = False,
    ) -> dict[str, Any]:
        self._require_face_service()
        prepared_image_bytes = self._prepare_camera_image_bytes(image_bytes)
        snapshot = self._store_snapshot(device_id, prepared_image_bytes)
        snapshot.user_id = user_id

        timestamp = int(time.time() * 1000)
        saved_path = self.face_service.save_user_image(
            user_id,
            f"{user_id}_cam{device_id}_{timestamp}.jpg",
            prepared_image_bytes,
        )
        saved_count = self.face_service.count_user_images(user_id)
        training_report = self.face_service.retrain_from_disk() if retrain else None

        return {
            "accepted": True,
            "device_id": device_id,
            "user_id": user_id,
            "saved_count": saved_count,
            "saved_path": str(saved_path),
            "snapshot_url": snapshot.image_url,
            "retrained": retrain,
            "training_report": training_report,
        }

    def _ensure_dirs(self) -> None:
        for path in [
            self.settings.data_dir,
            self.settings.faces_dir,
            self.settings.models_dir,
            self.settings.snapshot_dir,
        ]:
            Path(path).mkdir(parents=True, exist_ok=True)

    def _require_face_service(self) -> None:
        if self.face_service is None:
            raise RuntimeError(f"Face recognition is unavailable: {FACE_IMPORT_ERROR}")

    def _store_snapshot(self, device_id: int, image_bytes: bytes) -> CameraSnapshot:
        timestamp = int(time.time() * 1000)
        filename = f"esp32cam_{device_id}_{timestamp}.jpg"
        path = Path(self.settings.snapshot_dir) / filename
        path.write_bytes(image_bytes)
        snapshot = CameraSnapshot(
            device_id=device_id,
            filename=filename,
            image_url=f"/camera-log/files/{filename}",
            created_at=time.time(),
        )
        self.snapshots.append(snapshot)
        self._trim_camera_snapshots()
        return snapshot

    def _trim_camera_snapshots(self) -> None:
        active_filenames = {snapshot.filename for snapshot in self.snapshots}
        for file_path in Path(self.settings.snapshot_dir).glob("esp32cam_*.jpg"):
            if file_path.name not in active_filenames:
                file_path.unlink(missing_ok=True)

    def _prepare_camera_image_bytes(self, image_bytes: bytes) -> bytes:
        if not image_bytes or not self.settings.camera_rotate_180:
            return image_bytes

        try:
            import cv2
            import numpy as np
        except ModuleNotFoundError:
            return image_bytes

        frame = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return image_bytes

        rotated = cv2.rotate(frame, cv2.ROTATE_180)
        success, encoded = cv2.imencode(".jpg", rotated)
        return encoded.tobytes() if success else image_bytes

    def _recognition_candidates(self, image_bytes: bytes) -> list[tuple[str, bytes]]:
        if not image_bytes:
            return [("original", image_bytes)]

        prepared_image_bytes = self._prepare_camera_image_bytes(image_bytes)
        if prepared_image_bytes == image_bytes:
            return [("original", image_bytes)]
        return [
            ("rotated_180", prepared_image_bytes),
            ("original", image_bytes),
        ]
