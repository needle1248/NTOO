from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, RLock
from typing import Iterable

import cv2
import numpy as np

from app.config import Settings
from app.models import FacePrediction
from app.services.face_map import (
    FaceMapError,
    MIN_QUALITY_SCORE,
    MODEL_VERSION,
    build_face_map_from_bytes,
    build_face_maps_from_bytes,
)


@dataclass
class TrainedFaceModel:
    labels: list[str]
    threshold: float
    backend: str
    mean: np.ndarray | None = None
    components: np.ndarray | None = None
    centroids: np.ndarray | None = None
    embeddings: np.ndarray | None = None
    quality_scores: np.ndarray | None = None
    model_version: str | None = None


@dataclass
class DetectedFace:
    crop: np.ndarray
    count: int
    area_ratio: float


class FaceRecognitionService:
    MIN_FACE_AREA_RATIO = 0.015
    SFACE_CONFIDENCE_DISTANCE = 1.5

    def __init__(self, settings: Settings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.model_path = settings.models_dir / "face_model.npz"
        self.meta_path = settings.models_dir / "face_model_meta.json"
        self.detector_model_path = (
            settings.face_detector_model_path
            or (settings.models_dir / "face_detection_yunet_2023mar.onnx")
        )
        self.embedding_model_path = (
            settings.face_embedding_model_path
            or (settings.models_dir / "face_recognition_sface_2021dec.onnx")
        )
        self.detector = cv2.CascadeClassifier(
            str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
        )
        self.model: TrainedFaceModel | None = None
        self._detector_lock = Lock()
        self._model_lock = RLock()

    def load_if_available(self) -> bool:
        with self._model_lock:
            if not self.model_path.exists() or not self.meta_path.exists():
                return False

            meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            backend = str(meta.get("backend", "simple_pca"))
            preferred_backend = self._preferred_backend()
            if backend != preferred_backend:
                return False

            data = np.load(self.model_path)
            if backend == "sface":
                self.model = TrainedFaceModel(
                    labels=[str(item) for item in data["labels"].tolist()],
                    embeddings=data["embeddings"].astype(np.float32),
                    quality_scores=(
                        data["quality_scores"].astype(np.float32)
                        if "quality_scores" in data.files
                        else None
                    ),
                    threshold=float(meta["threshold"]),
                    backend="sface",
                    model_version=str(meta.get("model_version", MODEL_VERSION)),
                )
                return True

            self.model = TrainedFaceModel(
                labels=[str(item) for item in data["labels"].tolist()],
                mean=data["mean"].astype(np.float32),
                components=data["components"].astype(np.float32),
                centroids=data["centroids"].astype(np.float32),
                threshold=float(meta["threshold"]),
                backend=str(meta.get("backend", "simple_pca")),
            )
            return True

    def ensure_model(self) -> bool:
        with self._model_lock:
            preferred_backend = self._preferred_backend()
            if self.model is not None and self.model.backend == preferred_backend:
                return True
            if self.load_if_available():
                return True
            return self.retrain_from_disk().get("samples", 0) > 0

    def retrain_from_disk(self) -> dict[str, object]:
        with self._model_lock:
            if self._preferred_backend() == "sface":
                report = self._retrain_sface_from_disk()
                if report.get("samples", 0) > 0:
                    return report
                self.logger.warning(
                    "SFace retraining produced no usable samples, falling back to simple_pca."
                )
            return self._retrain_simple_pca_from_disk()

    def register_user_images(self, user_id: str, images: Iterable[tuple[str, bytes]]) -> dict[str, object]:
        saved = 0
        skipped_no_face = 0
        for index, (filename, content) in enumerate(images, start=1):
            if not self.image_has_face(content, relax=True):
                skipped_no_face += 1
                continue
            self.save_user_image(user_id, filename, content, preferred_index=index)
            saved += 1

        train_report = self.retrain_from_disk()
        train_report["saved"] = saved
        train_report["skipped_no_face"] = skipped_no_face
        train_report["user_id"] = user_id
        return train_report

    def save_user_image(
        self,
        user_id: str,
        filename: str,
        content: bytes,
        preferred_index: int | None = None,
    ) -> Path:
        user_dir = self.settings.faces_dir / user_id
        user_dir.mkdir(parents=True, exist_ok=True)

        suffix = Path(filename).suffix.lower() or ".jpg"
        if preferred_index is not None:
            candidate = user_dir / f"{user_id}_{preferred_index:02d}{suffix}"
            if not candidate.exists():
                candidate.write_bytes(content)
                return candidate

        next_index = self.count_user_images(user_id) + 1
        path = user_dir / f"{user_id}_{next_index:02d}{suffix}"
        path.write_bytes(content)
        return path

    def count_user_images(self, user_id: str) -> int:
        user_dir = self.settings.faces_dir / user_id
        if not user_dir.exists():
            return 0
        return sum(
            1
            for image_path in user_dir.iterdir()
            if image_path.is_file() and image_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp"}
        )

    def image_has_face(self, image_bytes: bytes, relax: bool = False) -> bool:
        if self._preferred_backend() == "sface":
            try:
                face_maps = build_face_maps_from_bytes(
                    image_bytes,
                    detector_model_path=self.detector_model_path,
                    embedding_model_path=self.embedding_model_path,
                    min_quality=0.0,
                )
            except FaceMapError:
                return False
            return bool(face_maps) if relax else len(face_maps) == 1

        frame = self._decode_image(image_bytes)
        if frame is None:
            return False
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return self._detect_face(gray, relax=relax) is not None

    def predict(self, image_bytes: bytes, device_id: int | None = None) -> FacePrediction:
        if not self.ensure_model() or self.model is None:
            return FacePrediction(
                matched=False,
                user_id=None,
                confidence=0.0,
                backend=self._preferred_backend(),
                threshold=self.settings.face_match_threshold,
                device_id=device_id,
            )

        if self.model.backend == "sface":
            return self._predict_sface(image_bytes, device_id=device_id)

        return self._predict_simple_pca(image_bytes, device_id=device_id)

    def get_status(self) -> dict[str, object]:
        with self._model_lock:
            if self.model is None and not self.load_if_available():
                return {
                    "loaded": False,
                    "backend": self._preferred_backend(),
                    "known_users": [],
                    "threshold": self.settings.face_match_threshold,
                }

            assert self.model is not None
            return {
                "loaded": True,
                "backend": self.model.backend,
                "known_users": sorted(set(self.model.labels)),
                "threshold": self.model.threshold,
            }

    def _predict_sface(self, image_bytes: bytes, device_id: int | None = None) -> FacePrediction:
        assert self.model is not None
        assert self.model.embeddings is not None

        try:
            probe_map = build_face_map_from_bytes(
                image_bytes,
                detector_model_path=self.detector_model_path,
                embedding_model_path=self.embedding_model_path,
                min_quality=0.0,
            )
        except FaceMapError:
            return FacePrediction(
                matched=False,
                user_id=None,
                confidence=0.0,
                backend=self.model.backend,
                threshold=self.model.threshold,
                device_id=device_id,
            )

        probe_embedding = np.array(probe_map["embedding"], dtype=np.float32)
        distances = np.linalg.norm(self.model.embeddings - probe_embedding, axis=1)
        best_by_user: dict[str, float] = {}
        for label, distance in zip(self.model.labels, distances.tolist(), strict=False):
            best_distance = best_by_user.get(label)
            if best_distance is None or distance < best_distance:
                best_by_user[label] = distance

        best_user, best_distance = min(best_by_user.items(), key=lambda item: item[1])
        matched = best_distance <= self.model.threshold
        confidence = max(
            0.0,
            min(1.0, 1.0 - best_distance / self.SFACE_CONFIDENCE_DISTANCE),
        )
        return FacePrediction(
            matched=matched,
            user_id=best_user if matched else None,
            confidence=round(float(confidence), 4),
            backend=self.model.backend,
            threshold=self.model.threshold,
            device_id=device_id,
        )

    def _predict_simple_pca(self, image_bytes: bytes, device_id: int | None = None) -> FacePrediction:
        assert self.model is not None
        assert self.model.mean is not None
        assert self.model.components is not None
        assert self.model.centroids is not None

        feature = self._extract_feature(image_bytes, require_face=True)
        if feature is None:
            return FacePrediction(
                matched=False,
                user_id=None,
                confidence=0.0,
                backend=self.model.backend,
                threshold=self.model.threshold,
                device_id=device_id,
            )

        embedding = self._embed(feature, self.model.mean, self.model.components)
        similarities = self.model.centroids @ embedding
        best_index = int(np.argmax(similarities))
        best_score = float(similarities[best_index])
        matched = best_score >= self.model.threshold
        return FacePrediction(
            matched=matched,
            user_id=self.model.labels[best_index] if matched else None,
            confidence=max(0.0, min(1.0, best_score)),
            backend=self.model.backend,
            threshold=self.model.threshold,
            device_id=device_id,
        )

    def _retrain_sface_from_disk(self) -> dict[str, object]:
        embeddings: list[np.ndarray] = []
        labels: list[str] = []
        quality_scores: list[float] = []
        skipped = 0

        for user_dir in sorted(self.settings.faces_dir.glob("*")):
            if not user_dir.is_dir():
                continue
            user_id = user_dir.name
            for image_path in sorted(user_dir.glob("*")):
                if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp"}:
                    continue
                try:
                    image_bytes = image_path.read_bytes()
                    face_map = build_face_map_from_bytes(
                        image_bytes,
                        detector_model_path=self.detector_model_path,
                        embedding_model_path=self.embedding_model_path,
                        min_quality=MIN_QUALITY_SCORE,
                    )
                except (OSError, FaceMapError):
                    skipped += 1
                    continue

                embeddings.append(np.array(face_map["embedding"], dtype=np.float32))
                labels.append(user_id)
                quality_scores.append(float(face_map["quality_score"]))

        if not embeddings:
            self._clear_model_files()
            return {"trained": False, "samples": 0, "skipped": skipped, "users": []}

        model = TrainedFaceModel(
            labels=labels,
            embeddings=np.stack(embeddings).astype(np.float32),
            quality_scores=np.array(quality_scores, dtype=np.float32),
            threshold=self.settings.face_match_threshold,
            backend="sface",
            model_version=MODEL_VERSION,
        )
        self.model = model
        np.savez_compressed(
            self.model_path,
            labels=np.array(model.labels),
            embeddings=model.embeddings,
            quality_scores=model.quality_scores,
        )
        self.meta_path.write_text(
            json.dumps(
                {
                    "threshold": model.threshold,
                    "backend": model.backend,
                    "model_version": model.model_version,
                    "users": sorted(set(model.labels)),
                    "samples": len(embeddings),
                    "min_quality_score": MIN_QUALITY_SCORE,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return {
            "trained": True,
            "samples": len(embeddings),
            "skipped": skipped,
            "users": sorted(set(model.labels)),
            "backend": model.backend,
            "threshold": model.threshold,
        }

    def _retrain_simple_pca_from_disk(self) -> dict[str, object]:
        samples: list[np.ndarray] = []
        labels: list[str] = []
        skipped = 0

        for user_dir in sorted(self.settings.faces_dir.glob("*")):
            if not user_dir.is_dir():
                continue
            user_id = user_dir.name
            for image_path in sorted(user_dir.glob("*")):
                if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp"}:
                    continue
                try:
                    image_bytes = image_path.read_bytes()
                except OSError:
                    skipped += 1
                    continue
                feature = self._extract_feature(image_bytes, relax_detection=True)
                if feature is None:
                    skipped += 1
                    continue
                samples.append(feature)
                labels.append(user_id)

        if not samples:
            self._clear_model_files()
            return {"trained": False, "samples": 0, "skipped": skipped, "users": []}

        model = self._train_simple_pca(np.stack(samples), labels)
        self.model = model
        np.savez_compressed(
            self.model_path,
            labels=np.array(model.labels),
            mean=model.mean,
            components=model.components,
            centroids=model.centroids,
        )
        self.meta_path.write_text(
            json.dumps(
                {
                    "threshold": model.threshold,
                    "backend": model.backend,
                    "users": model.labels,
                    "samples": len(samples),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return {
            "trained": True,
            "samples": len(samples),
            "skipped": skipped,
            "users": model.labels,
            "backend": model.backend,
            "threshold": model.threshold,
        }

    def _clear_model_files(self) -> None:
        with self._model_lock:
            self.model = None
            if self.model_path.exists():
                self.model_path.unlink(missing_ok=True)
            if self.meta_path.exists():
                self.meta_path.unlink(missing_ok=True)

    def _preferred_backend(self) -> str:
        if self.settings.face_backend == "sface" and self._supports_sface():
            return "sface"
        return "simple_pca"

    def _supports_sface(self) -> bool:
        return hasattr(cv2, "FaceDetectorYN") and hasattr(cv2, "FaceRecognizerSF")

    def _extract_feature(
        self,
        image_bytes: bytes,
        require_face: bool = True,
        relax_detection: bool = False,
    ) -> np.ndarray | None:
        if not image_bytes:
            return None

        frame = self._decode_image(image_bytes)
        if frame is None:
            return None

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        detected_face = self._detect_face(gray, relax=relax_detection)
        if detected_face is None:
            if require_face:
                return None
            face = self._center_crop(gray)
        else:
            face = detected_face.crop

        face = cv2.equalizeHist(face)
        face = cv2.resize(
            face,
            (self.settings.face_image_size, self.settings.face_image_size),
            interpolation=cv2.INTER_AREA,
        )
        return face.astype(np.float32).reshape(-1) / 255.0

    def _decode_image(self, image_bytes: bytes) -> np.ndarray | None:
        return cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)

    def _detect_face(self, gray: np.ndarray, relax: bool = False) -> DetectedFace | None:
        if self.detector.empty():
            return None

        if gray.ndim != 2:
            return None
        if gray.shape[0] < 32 or gray.shape[1] < 32:
            return None

        try:
            with self._detector_lock:
                faces = self.detector.detectMultiScale(
                    gray,
                    scaleFactor=1.05 if relax else 1.08,
                    minNeighbors=4 if relax else 6,
                    minSize=(28, 28) if relax else (40, 40),
                )
        except cv2.error as exc:
            self.logger.warning("OpenCV Haar face detection failed: %s", exc)
            return None

        if len(faces) == 0:
            return None

        height, width = gray.shape[:2]
        frame_area = float(height * width)
        x, y, w, h = max(faces, key=lambda item: int(item[2]) * int(item[3]))
        area_ratio = float(w * h) / frame_area if frame_area else 0.0

        if not relax and len(faces) != 1:
            return None
        if area_ratio < (0.008 if relax else self.MIN_FACE_AREA_RATIO):
            return None

        return DetectedFace(
            crop=gray[y : y + h, x : x + w],
            count=len(faces),
            area_ratio=area_ratio,
        )

    def _center_crop(self, gray: np.ndarray) -> np.ndarray:
        height, width = gray.shape[:2]
        size = min(height, width)
        offset_x = max(0, (width - size) // 2)
        offset_y = max(0, (height - size) // 2)
        return gray[offset_y : offset_y + size, offset_x : offset_x + size]

    def _train_simple_pca(self, raw_samples: np.ndarray, labels: list[str]) -> TrainedFaceModel:
        mean = raw_samples.mean(axis=0).astype(np.float32)
        centered = raw_samples - mean

        if len(raw_samples) >= 3:
            _, _, vt = np.linalg.svd(centered, full_matrices=False)
            components_count = min(32, len(raw_samples) - 1, vt.shape[0])
            components = vt[:components_count].astype(np.float32)
        else:
            components = np.empty((0, raw_samples.shape[1]), dtype=np.float32)

        embeddings = np.stack([self._embed(sample, mean, components) for sample in raw_samples])
        unique_labels = sorted(set(labels))
        centroids: list[np.ndarray] = []

        for user_id in unique_labels:
            user_embeddings = embeddings[[index for index, label in enumerate(labels) if label == user_id]]
            centroid = user_embeddings.mean(axis=0)
            centroid = centroid / (np.linalg.norm(centroid) or 1.0)
            centroids.append(centroid.astype(np.float32))

        return TrainedFaceModel(
            labels=unique_labels,
            mean=mean,
            components=components,
            centroids=np.stack(centroids),
            threshold=self.settings.face_match_threshold,
            backend="simple_pca",
        )

    def _embed(self, feature: np.ndarray, mean: np.ndarray, components: np.ndarray) -> np.ndarray:
        centered = feature - mean
        if components.size:
            embedding = centered @ components.T
        else:
            embedding = centered
        norm = np.linalg.norm(embedding) or 1.0
        return (embedding / norm).astype(np.float32)
