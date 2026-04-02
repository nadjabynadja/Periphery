"""CCTV frame detection pipeline using YOLOv8, DeepFace, and custom models."""

import io
import logging
import os
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# ── Lazy-loaded model singletons ──────────────────────────────────

_yolo_model = None
_deepface_available: Optional[bool] = None

# YOLO COCO classes we care about (id -> label)
_RELEVANT_CLASSES = {
    0: "person",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
    24: "backpack",
    26: "handbag",
    28: "suitcase",
    43: "knife",
    76: "scissors",
    67: "cell phone",
}

# Category mapping
_CATEGORY_MAP = {
    "person": "person",
    "car": "vehicle",
    "motorcycle": "vehicle",
    "bus": "vehicle",
    "truck": "vehicle",
    "backpack": "accessory",
    "handbag": "accessory",
    "suitcase": "accessory",
    "knife": "weapon",
    "scissors": "weapon",
    "cell phone": "electronic",
}

_VEHICLE_LABELS = {"car", "truck"}


def _get_yolo_model():
    """Lazy-load YOLOv8 model on first call."""
    global _yolo_model
    if _yolo_model is None:
        from ultralytics import YOLO

        _yolo_model = YOLO("yolov8n.pt")  # nano model for speed
        logger.info("YOLOv8n model loaded")
    return _yolo_model


def _check_deepface():
    """Check if DeepFace + tensorflow are available."""
    global _deepface_available
    if _deepface_available is None:
        try:
            import deepface  # noqa: F401

            _deepface_available = True
            logger.info("DeepFace available")
        except Exception:
            _deepface_available = False
            logger.warning("DeepFace not available — face matching disabled")
    return _deepface_available


def _bytes_to_image(frame_bytes: bytes) -> Image.Image:
    """Convert raw image bytes to PIL Image."""
    return Image.open(io.BytesIO(frame_bytes)).convert("RGB")


def _bytes_to_ndarray(frame_bytes: bytes) -> np.ndarray:
    """Convert raw image bytes to numpy array (RGB)."""
    img = _bytes_to_image(frame_bytes)
    return np.array(img)


# ── Detection Functions ───────────────────────────────────────────


def detect_objects(frame_bytes: bytes) -> list[dict]:
    """Run YOLOv8 on a frame, return detections with bounding boxes.

    Filters for relevant classes: person, car, truck, bus, motorcycle,
    knife, scissors, cell phone, backpack, handbag, suitcase.
    Maps knife/scissors to 'weapon' category.
    """
    try:
        model = _get_yolo_model()
        img = _bytes_to_ndarray(frame_bytes)
        h, w = img.shape[:2]

        results = model(img, verbose=False)
        detections = []

        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                cls_id = int(box.cls[0])
                if cls_id not in _RELEVANT_CLASSES:
                    continue

                label = _RELEVANT_CLASSES[cls_id]
                conf = float(box.conf[0])
                x1, y1, x2, y2 = box.xyxy[0].tolist()

                # Normalize to 0-1
                bx = x1 / w
                by = y1 / h
                bw = (x2 - x1) / w
                bh = (y2 - y1) / h

                # Map knife/scissors label to "weapon"
                display_label = "weapon" if label in ("knife", "scissors") else label

                detections.append(
                    {
                        "category": _CATEGORY_MAP[label],
                        "label": display_label,
                        "confidence": round(conf, 4),
                        "boundingBox": {
                            "x": round(bx, 4),
                            "y": round(by, 4),
                            "w": round(bw, 4),
                            "h": round(bh, 4),
                        },
                    }
                )

        return detections

    except Exception as e:
        logger.error(f"Object detection failed: {e}")
        return []


def detect_license_plates(frame_bytes: bytes) -> list[dict]:
    """Detect potential license plates by finding vehicle bounding boxes.

    Uses the standard YOLO model to find car/truck detections as plate
    candidates. Returns bbox + 'plate_candidate' label. A dedicated
    license plate model or OCR stage can refine these later.
    """
    try:
        model = _get_yolo_model()
        img = _bytes_to_ndarray(frame_bytes)
        h, w = img.shape[:2]

        results = model(img, verbose=False)
        detections = []

        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                cls_id = int(box.cls[0])
                label = _RELEVANT_CLASSES.get(cls_id)
                if label not in _VEHICLE_LABELS:
                    continue

                conf = float(box.conf[0])
                x1, y1, x2, y2 = box.xyxy[0].tolist()

                # Estimate plate region: bottom-center third of vehicle bbox
                plate_x1 = x1 + (x2 - x1) * 0.2
                plate_y1 = y1 + (y2 - y1) * 0.65
                plate_x2 = x1 + (x2 - x1) * 0.8
                plate_y2 = y2

                bx = plate_x1 / w
                by = plate_y1 / h
                bw = (plate_x2 - plate_x1) / w
                bh = (plate_y2 - plate_y1) / h

                detections.append(
                    {
                        "category": "license_plate",
                        "label": "plate_candidate",
                        "confidence": round(conf * 0.6, 4),  # discount confidence
                        "boundingBox": {
                            "x": round(bx, 4),
                            "y": round(by, 4),
                            "w": round(bw, 4),
                            "h": round(bh, 4),
                        },
                    }
                )

        return detections

    except Exception as e:
        logger.error(f"License plate detection failed: {e}")
        return []


def detect_faces_against_watchlist(
    frame_bytes: bytes, watchlist_dir: str = "/app/data/watchlists"
) -> list[dict]:
    """Detect faces and compare against a watchlist directory of reference images.

    Uses DeepFace for face detection and comparison. Each subdirectory in
    watchlist_dir is a watchlist category (e.g., 'wanted', 'fugitives').
    Reference images should be named 'LASTNAME_FIRSTNAME.ext'.

    Returns matches with confidence and watchlist name.
    """
    if not _check_deepface():
        logger.warning("DeepFace unavailable — skipping face matching")
        return []

    try:
        from deepface import DeepFace

        img = _bytes_to_ndarray(frame_bytes)
        h, w = img.shape[:2]
        detections = []

        watchlist_path = Path(watchlist_dir)
        if not watchlist_path.exists():
            logger.warning(f"Watchlist directory not found: {watchlist_dir}")
            return []

        # Collect all reference images grouped by watchlist
        ref_images: list[tuple[str, str, str]] = []  # (path, watchlist_name, person_label)
        for subdir in watchlist_path.iterdir():
            if not subdir.is_dir():
                continue
            watchlist_name = subdir.name
            for img_file in subdir.iterdir():
                if img_file.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                    person_label = img_file.stem.replace("_", " ").title()
                    ref_images.append((str(img_file), watchlist_name, person_label))

        if not ref_images:
            return []

        # Detect faces in the frame first
        try:
            face_objs = DeepFace.extract_faces(
                img, detector_backend="opencv", enforce_detection=False
            )
        except Exception:
            return []

        # Compare each detected face against reference images
        for face_obj in face_objs:
            if face_obj.get("confidence", 0) < 0.5:
                continue

            facial_area = face_obj.get("facial_area", {})
            fx = facial_area.get("x", 0)
            fy = facial_area.get("y", 0)
            fw = facial_area.get("w", 0)
            fh = facial_area.get("h", 0)

            for ref_path, watchlist_name, person_label in ref_images:
                try:
                    result = DeepFace.verify(
                        img1_path=img,
                        img2_path=ref_path,
                        enforce_detection=False,
                        model_name="VGG-Face",
                    )
                    if result.get("verified", False):
                        distance = result.get("distance", 1.0)
                        confidence = max(0.0, 1.0 - distance)

                        detections.append(
                            {
                                "category": "face_match",
                                "label": person_label,
                                "confidence": round(confidence, 4),
                                "boundingBox": {
                                    "x": round(fx / w, 4),
                                    "y": round(fy / h, 4),
                                    "w": round(fw / w, 4),
                                    "h": round(fh / h, 4),
                                },
                                "watchlist": watchlist_name,
                            }
                        )
                except Exception as e:
                    logger.debug(f"Face comparison failed for {ref_path}: {e}")
                    continue

        return detections

    except Exception as e:
        logger.error(f"Face detection/matching failed: {e}")
        return []


def detect_hate_symbols(frame_bytes: bytes) -> list[dict]:
    """Detect hate symbols in a frame.

    TODO: Train a custom classifier on the ADL Hate Symbols Database
    (https://www.adl.org/hate-symbols). This would involve:
    1. Scraping/collecting labeled images from the ADL database
    2. Fine-tuning a YOLOv8 classification model on the dataset
    3. Loading the custom weights here for inference

    For now, returns an empty list as a placeholder.
    """
    return []
