from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import numpy as np
from PIL import Image, ImageOps


MIN_FACE_SIZE = 80
MIN_QUALITY_SCORE = 0.35
MODEL_VERSION = "opencv-yunet-sface-onnx"
MAX_IMAGE_SIDE = 1600
DETECTOR_SCORE_THRESHOLD = 0.6
DETECTOR_NMS_THRESHOLD = 0.3
YUNET_MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/refs/heads/main/models/face_detection_yunet/"
    "face_detection_yunet_2023mar.onnx"
)
SFACE_MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/refs/heads/main/models/face_recognition_sface/"
    "face_recognition_sface_2021dec.onnx"
)


class FaceMapError(Exception):
    """Raised when a face map cannot be built."""


def build_face_map_from_bytes(
    payload: bytes,
    *,
    detector_model_path: Path,
    embedding_model_path: Path,
    min_quality: float = MIN_QUALITY_SCORE,
) -> dict[str, Any]:
    return _build_single_face_map(
        load_image_from_bytes(payload),
        source_image="camera-upload",
        detector_model_path=detector_model_path,
        embedding_model_path=embedding_model_path,
        min_quality=min_quality,
    )


def build_face_maps_from_bytes(
    payload: bytes,
    *,
    detector_model_path: Path,
    embedding_model_path: Path,
    min_quality: float = 0.0,
) -> list[dict[str, Any]]:
    return _build_face_map_collection(
        load_image_from_bytes(payload),
        source_image="camera-upload",
        detector_model_path=detector_model_path,
        embedding_model_path=embedding_model_path,
        min_quality=min_quality,
    )


def load_image_from_bytes(payload: bytes) -> np.ndarray:
    try:
        image = Image.open(BytesIO(payload))
        image = ImageOps.exif_transpose(image).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        raise FaceMapError(f"Unable to read image: {exc}") from exc

    if max(image.size) > MAX_IMAGE_SIDE:
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    return np.array(image)


def compute_blur_score(image: np.ndarray) -> float:
    grayscale = image.mean(axis=2)
    grad_y = np.diff(grayscale, axis=0)
    grad_x = np.diff(grayscale, axis=1)
    variance = float(np.var(grad_x)) + float(np.var(grad_y))
    return min(1.0, variance / 1000.0)


def compute_brightness_score(image: np.ndarray) -> float:
    grayscale = image.mean(axis=2) / 255.0
    mean_brightness = float(grayscale.mean())
    return max(0.0, 1.0 - min(abs(mean_brightness - 0.55) / 0.55, 1.0))


def compute_face_size_score(bbox: tuple[int, int, int, int], image_shape: tuple[int, ...]) -> float:
    top, right, bottom, left = bbox
    face_width = right - left
    face_height = bottom - top
    short_side = min(image_shape[0], image_shape[1])
    ratio = min(face_width, face_height) / max(short_side, 1)
    return min(1.0, ratio / 0.25)


def compute_edge_margin_score(
    bbox: tuple[int, int, int, int],
    image_shape: tuple[int, ...],
) -> float:
    top, right, bottom, left = bbox
    height, width = image_shape[:2]
    margin = min(top, left, width - right, height - bottom)
    margin_ratio = margin / max(min(width, height), 1)
    return min(1.0, max(0.0, margin_ratio / 0.08))


def estimate_quality(image: np.ndarray, bbox: tuple[int, int, int, int]) -> float:
    top, right, bottom, left = bbox
    face_crop = image[top:bottom, left:right]
    if face_crop.size == 0:
        return 0.0

    face_width = right - left
    face_height = bottom - top
    if min(face_width, face_height) < MIN_FACE_SIZE:
        return 0.0

    blur_score = compute_blur_score(face_crop)
    brightness_score = compute_brightness_score(face_crop)
    face_size_score = compute_face_size_score(bbox, image.shape)
    edge_margin_score = compute_edge_margin_score(bbox, image.shape)

    quality = (
        0.35 * blur_score
        + 0.20 * brightness_score
        + 0.30 * face_size_score
        + 0.15 * edge_margin_score
    )
    return round(max(0.0, min(1.0, quality)), 4)


def _build_single_face_map(
    image: np.ndarray,
    *,
    source_image: str,
    detector_model_path: Path,
    embedding_model_path: Path,
    min_quality: float,
) -> dict[str, Any]:
    _, image_bgr, faces, recognizer = _detect_faces(
        image,
        detector_model_path=detector_model_path,
        embedding_model_path=embedding_model_path,
    )
    if len(faces) > 1:
        raise FaceMapError("Multiple faces found in the image.")
    return _build_face_map_from_detection(
        image=image,
        image_bgr=image_bgr,
        face=faces[0],
        recognizer=recognizer,
        source_image=source_image,
        min_quality=min_quality,
    )


def _build_face_map_collection(
    image: np.ndarray,
    *,
    source_image: str,
    detector_model_path: Path,
    embedding_model_path: Path,
    min_quality: float,
) -> list[dict[str, Any]]:
    _, image_bgr, faces, recognizer = _detect_faces(
        image,
        detector_model_path=detector_model_path,
        embedding_model_path=embedding_model_path,
    )
    collected: list[dict[str, Any]] = []
    last_error: FaceMapError | None = None

    for face in faces:
        try:
            collected.append(
                _build_face_map_from_detection(
                    image=image,
                    image_bgr=image_bgr,
                    face=face,
                    recognizer=recognizer,
                    source_image=source_image,
                    min_quality=min_quality,
                )
            )
        except FaceMapError as exc:
            last_error = exc

    if collected:
        return collected
    if last_error is not None:
        raise last_error
    raise FaceMapError("No usable faces found in the image.")


def _detect_faces(
    image: np.ndarray,
    *,
    detector_model_path: Path,
    embedding_model_path: Path,
):
    cv2 = _load_cv2()
    _ensure_model_file(detector_model_path, YUNET_MODEL_URL)
    _ensure_model_file(embedding_model_path, SFACE_MODEL_URL)

    image_bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    height, width = image.shape[:2]
    detector = cv2.FaceDetectorYN.create(
        str(detector_model_path),
        "",
        (width, height),
        score_threshold=DETECTOR_SCORE_THRESHOLD,
        nms_threshold=DETECTOR_NMS_THRESHOLD,
        top_k=5000,
    )
    detector.setInputSize((width, height))
    _, faces = detector.detect(image_bgr)

    if faces is None or len(faces) == 0:
        raise FaceMapError("No face found in the image.")

    recognizer = cv2.FaceRecognizerSF.create(str(embedding_model_path), "")
    sorted_faces = sorted(faces, key=lambda item: (float(item[0]), float(item[1])))
    return cv2, image_bgr, sorted_faces, recognizer


def _build_face_map_from_detection(
    *,
    image: np.ndarray,
    image_bgr: np.ndarray,
    face: np.ndarray,
    recognizer: Any,
    source_image: str,
    min_quality: float,
) -> dict[str, Any]:
    bbox = _extract_bbox(face)
    quality_score = estimate_quality(image, bbox)
    if quality_score < min_quality:
        raise FaceMapError(
            f"Low quality image. quality_score={quality_score}, required>={min_quality}"
        )

    aligned_face = recognizer.alignCrop(image_bgr, face)
    embedding = recognizer.feature(aligned_face)
    if embedding is None or embedding.size == 0:
        raise FaceMapError("Unable to extract face embedding.")

    embedding = embedding.flatten().astype(np.float32)
    norm = np.linalg.norm(embedding)
    if norm == 0:
        raise FaceMapError("Unable to normalize face embedding.")

    embedding = embedding / norm
    return {
        "embedding": [round(float(value), 8) for value in embedding.tolist()],
        "embedding_size": int(len(embedding)),
        "bbox": {
            "top": int(bbox[0]),
            "right": int(bbox[1]),
            "bottom": int(bbox[2]),
            "left": int(bbox[3]),
            "width": int(bbox[1] - bbox[3]),
            "height": int(bbox[2] - bbox[0]),
        },
        "landmarks": _extract_landmarks(face),
        "quality_score": quality_score,
        "model_version": MODEL_VERSION,
        "source_image": source_image,
    }


def _load_cv2():
    try:
        import cv2
    except ModuleNotFoundError as exc:
        raise FaceMapError(
            "Dependency 'opencv-python' is not installed. Run: pip install opencv-python"
        ) from exc

    if not hasattr(cv2, "FaceDetectorYN") or not hasattr(cv2, "FaceRecognizerSF"):
        raise FaceMapError(
            "OpenCV build does not support FaceDetectorYN / FaceRecognizerSF."
        )
    return cv2


def _ensure_model_file(path: Path, url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not _is_lfs_pointer(path):
        return

    try:
        with urlopen(url, timeout=30) as response:
            payload = response.read()
    except Exception as exc:  # noqa: BLE001
        raise FaceMapError(f"Unable to download model {path.name}: {exc}") from exc

    if payload.startswith(b"version https://git-lfs.github.com/spec/"):
        raise FaceMapError(f"Downloaded Git LFS pointer instead of model for {path.name}.")
    path.write_bytes(payload)


def _extract_bbox(face: np.ndarray) -> tuple[int, int, int, int]:
    x, y, w, h = face[:4]
    left = int(round(float(x)))
    top = int(round(float(y)))
    right = int(round(float(x + w)))
    bottom = int(round(float(y + h)))
    return top, right, bottom, left


def _extract_landmarks(face: np.ndarray) -> dict[str, Any]:
    points = [
        (float(face[4]), float(face[5])),
        (float(face[6]), float(face[7])),
        (float(face[8]), float(face[9])),
        (float(face[10]), float(face[11])),
        (float(face[12]), float(face[13])),
    ]
    left_eye, right_eye, nose_tip, mouth_left, mouth_right = points
    return {
        "keypoints": {
            "left_eye_center": [round(left_eye[0], 2), round(left_eye[1], 2)],
            "right_eye_center": [round(right_eye[0], 2), round(right_eye[1], 2)],
            "nose_tip_center": [round(nose_tip[0], 2), round(nose_tip[1], 2)],
            "mouth_left": [round(mouth_left[0], 2), round(mouth_left[1], 2)],
            "mouth_right": [round(mouth_right[0], 2), round(mouth_right[1], 2)],
        }
    }


def _is_lfs_pointer(path: Path) -> bool:
    try:
        header = path.read_bytes()[:64]
    except Exception:  # noqa: BLE001
        return False
    return header.startswith(b"version https://git-lfs.github.com/spec/")
