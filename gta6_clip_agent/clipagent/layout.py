"""Pick a vertical layout by finding the creator's face in the footage.

  stack - a small face-cam was found: creator on the top half, the full scene on
          the bottom half (the standard streamer-clip layout)
  crop  - the creator fills the frame: crop 9:16 centred on their face
  blur  - no steady face: the whole 16:9 frame over a blurred background
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)
PROBE_W = 640


def detect_face(path: Path, start_s: float, end_s: float, samples: int = 7) -> tuple[float, float, float, float] | None:
    """Median face box as fractions of the frame (x, y, w, h), or None if no steady face."""
    try:
        import cv2
    except ImportError:
        return None
    cascade_path = getattr(getattr(cv2, "data", None), "haarcascades", "") + "haarcascade_frontalface_default.xml"
    if not Path(cascade_path).exists():
        log.info("OpenCV face model not bundled (use opencv-python-headless<5); using blur layout")
        return None
    cascade = cv2.CascadeClassifier(cascade_path)
    boxes = []
    for i in range(samples):
        t = start_s + (end_s - start_s) * (i + 0.5) / samples
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
             "-vf", f"scale={PROBE_W}:-2,format=gray", "-f", "rawvideo", "-"],
            capture_output=True,
        ).stdout
        if not raw or len(raw) % PROBE_W:
            continue
        h = len(raw) // PROBE_W
        img = np.frombuffer(raw, dtype=np.uint8).reshape(h, PROBE_W)
        faces = cascade.detectMultiScale(img, scaleFactor=1.1, minNeighbors=6, minSize=(36, 36))
        if len(faces):
            x, y, w, fh = max(faces, key=lambda f: f[2] * f[3])
            boxes.append((x / PROBE_W, y / h, w / PROBE_W, fh / h))
    if len(boxes) < max(2, samples // 2):
        return None
    arr = np.array(boxes)
    # A face-cam stays put; a face that jumps around is gameplay or a false hit.
    if arr[:, 0].std() > 0.12 or arr[:, 1].std() > 0.12:
        return None
    return tuple(float(v) for v in np.median(arr, axis=0))


def choose(face: tuple[float, float, float, float] | None) -> str:
    if face is None:
        return "blur"
    return "stack" if face[2] < 0.28 else "crop"
