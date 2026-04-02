"""Lip reading integration — wrapper around Computer-Vision-Lip-Reading-2.0.

The lip reading model uses TensorFlow/Keras and dlib for face landmark detection.
It recognizes a set of predefined words from video frames of lips moving.

Model repo: https://github.com/allenye66/Computer-Vision-Lip-Reading-2.0
Cloned to: /root/lip-reading (or /app/lip-reading in Docker)

This is a heavy model — only loaded on demand when lip reading is requested.
"""

import logging
import os
import sys
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

LIP_READING_DIR = os.environ.get("LIP_READING_DIR", "/root/lip-reading")

_model = None
_face_detector = None
_landmark_predictor = None
_initialized = False


def _ensure_initialized():
    """Lazy-load the lip reading model and dlib predictors."""
    global _model, _face_detector, _landmark_predictor, _initialized
    if _initialized:
        return _model is not None

    _initialized = True

    model_dir = Path(LIP_READING_DIR)
    if not model_dir.exists():
        logger.warning(f"Lip reading directory not found: {model_dir}")
        return False

    try:
        import dlib
        import tensorflow as tf

        # Load dlib face detector and landmark predictor
        _face_detector = dlib.get_frontal_face_detector()

        predictor_path = model_dir / "model" / "shape_predictor_68_face_landmarks.dat"
        if not predictor_path.exists():
            # Try common alternative locations
            for alt in [
                "/root/lip-reading/shape_predictor_68_face_landmarks.dat",
                "/app/data/shape_predictor_68_face_landmarks.dat",
            ]:
                if os.path.exists(alt):
                    predictor_path = Path(alt)
                    break

        if predictor_path.exists():
            _landmark_predictor = dlib.shape_predictor(str(predictor_path))
        else:
            logger.warning(f"dlib shape predictor not found at {predictor_path}")
            logger.info("Download from: http://dlib.net/files/shape_predictor_68_face_landmarks.dat.bz2")
            return False

        # Load the trained Keras model
        model_path = model_dir / "model" / "lip_reading_model.h5"
        if not model_path.exists():
            # Search for any .h5 file
            h5_files = list(model_dir.rglob("*.h5"))
            if h5_files:
                model_path = h5_files[0]
            else:
                logger.warning(f"No trained model found in {model_dir}")
                return False

        _model = tf.keras.models.load_model(str(model_path))
        logger.info(f"Lip reading model loaded from {model_path}")
        return True

    except ImportError as e:
        logger.warning(f"Lip reading dependencies not available: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to initialize lip reading: {e}")
        return False


def extract_lip_region(frame: np.ndarray) -> Optional[np.ndarray]:
    """Extract the lip region from a face in a frame.

    Uses dlib's 68-point face landmark detector.
    Lip landmarks are points 48-67.
    """
    if not _ensure_initialized() or _face_detector is None or _landmark_predictor is None:
        return None

    try:
        import cv2

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
        faces = _face_detector(gray, 1)

        if not faces:
            return None

        # Use the largest face
        face = max(faces, key=lambda f: (f.right() - f.left()) * (f.bottom() - f.top()))
        landmarks = _landmark_predictor(gray, face)

        # Lip landmarks: 48-67
        lip_points = []
        for i in range(48, 68):
            lip_points.append((landmarks.part(i).x, landmarks.part(i).y))

        lip_points = np.array(lip_points)

        # Bounding box around lips with padding
        x_min = max(lip_points[:, 0].min() - 10, 0)
        x_max = min(lip_points[:, 0].max() + 10, frame.shape[1])
        y_min = max(lip_points[:, 1].min() - 5, 0)
        y_max = min(lip_points[:, 1].max() + 5, frame.shape[0])

        lip_region = frame[y_min:y_max, x_min:x_max]

        # Resize to model input size (typically 100x50 or similar)
        lip_resized = cv2.resize(lip_region, (100, 50))
        return lip_resized

    except Exception as e:
        logger.error(f"Lip region extraction failed: {e}")
        return None


# The predefined words the model can recognize
VOCABULARY = [
    "hello", "goodbye", "yes", "no", "please",
    "thank you", "stop", "go", "help", "left",
    "right", "up", "down", "open", "close",
]


async def predict_from_frames(frames: list[np.ndarray]) -> Optional[dict]:
    """Run lip reading prediction on a sequence of frames.

    The model expects a sequence of ~22 lip-region frames.

    Args:
        frames: List of video frames (numpy arrays, BGR format)

    Returns:
        dict with: word, confidence, frame_count
        None if prediction fails
    """
    if not _ensure_initialized() or _model is None:
        return None

    try:
        # Extract lip regions from each frame
        lip_frames = []
        for frame in frames:
            lip = extract_lip_region(frame)
            if lip is not None:
                lip_frames.append(lip)

        if len(lip_frames) < 10:
            return None  # Not enough lip frames

        # Pad or truncate to 22 frames (model's expected input)
        target_len = 22
        if len(lip_frames) > target_len:
            # Sample evenly
            indices = np.linspace(0, len(lip_frames) - 1, target_len, dtype=int)
            lip_frames = [lip_frames[i] for i in indices]
        elif len(lip_frames) < target_len:
            # Pad with last frame
            while len(lip_frames) < target_len:
                lip_frames.append(lip_frames[-1])

        # Stack and normalize
        lip_array = np.array(lip_frames, dtype=np.float32) / 255.0
        lip_array = np.expand_dims(lip_array, axis=0)  # Batch dimension

        # Predict
        predictions = _model.predict(lip_array, verbose=0)
        predicted_idx = np.argmax(predictions[0])
        confidence = float(predictions[0][predicted_idx])

        word = VOCABULARY[predicted_idx] if predicted_idx < len(VOCABULARY) else f"word_{predicted_idx}"

        return {
            "word": word,
            "confidence": round(confidence, 3),
            "frame_count": len(frames),
            "category": "lipreading",
            "label": f"Lip: '{word}'",
        }

    except Exception as e:
        logger.error(f"Lip reading prediction failed: {e}")
        return None


def is_available() -> bool:
    """Check if lip reading model is available."""
    return _ensure_initialized()
