"""Find the most "clip-worthy" moments in a long gameplay recording.

Pure signal processing with ffmpeg + numpy, no model calls:
  * audio energy  - explosions, gunfire, crashes, your own reactions
  * audio onsets  - sudden spikes (the "oh!" moment)
  * motion        - frame-to-frame pixel change (chases, crashes, fast action)
  * cuts          - large motion spikes (scene change / respawn / camera switch)

Each signal is sampled at ``FPS`` Hz, z-scored and combined into an "excitement"
curve. We slide windows of different lengths across it, reward windows whose
peak lands early (a strong hook in the first ~3 seconds is what keeps people
from swiping) and keep the top non-overlapping windows.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

FPS = 4  # analysis samples per second
AUDIO_SR = 16000
THUMB_W, THUMB_H = 64, 36


@dataclass
class Candidate:
    source: str
    start_s: float
    end_s: float
    score: float
    peak_s: float  # absolute time of the most intense moment

    @property
    def duration(self) -> float:
        return self.end_s - self.start_s

    def to_dict(self) -> dict:
        return asdict(self)


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def has_audio(path: Path) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout
    return bool(out.strip())


def audio_energy(path: Path, n_samples: int) -> tuple[np.ndarray, np.ndarray]:
    """Return (rms_energy, onset_strength), each resampled to ``n_samples`` bins."""
    if not has_audio(path):
        z = np.zeros(n_samples)
        return z, z
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(AUDIO_SR),
         "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    hop = AUDIO_SR // FPS
    n = min(n_samples, len(pcm) // hop)
    if n == 0:
        z = np.zeros(n_samples)
        return z, z
    frames = pcm[: n * hop].reshape(n, hop)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    loud_db = 20 * np.log10(rms + 1e-6)
    onset = np.maximum(np.diff(loud_db, prepend=loud_db[0]), 0)
    return _fit(loud_db, n_samples), _fit(onset, n_samples)


def motion(path: Path, n_samples: int) -> np.ndarray:
    """Mean absolute grayscale difference between consecutive tiny thumbnails."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-an",
         "-vf", f"fps={FPS},scale={THUMB_W}:{THUMB_H},format=gray",
         "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    ).stdout
    frames = np.frombuffer(raw, dtype=np.uint8)
    n = len(frames) // (THUMB_W * THUMB_H)
    if n < 2:
        return np.zeros(n_samples)
    frames = frames[: n * THUMB_W * THUMB_H].reshape(n, THUMB_H, THUMB_W).astype(np.float32)
    diff = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    diff = np.concatenate([[0.0], diff])
    return _fit(diff, n_samples)


def _fit(x: np.ndarray, n: int) -> np.ndarray:
    if len(x) == n:
        return x
    if len(x) > n:
        return x[:n]
    return np.pad(x, (0, n - len(x)), mode="edge" if len(x) else "constant")


def _z(x: np.ndarray) -> np.ndarray:
    std = x.std()
    return (x - x.mean()) / std if std > 1e-9 else np.zeros_like(x)


def _smooth(x: np.ndarray, seconds: float) -> np.ndarray:
    k = max(1, int(seconds * FPS))
    return np.convolve(x, np.ones(k) / k, mode="same")


def excitement_curve(path: Path) -> np.ndarray:
    duration = probe_duration(path)
    n = max(1, int(duration * FPS))
    loud, onset = audio_energy(path, n)
    mot = motion(path, n)
    cuts = (mot > np.percentile(mot, 99)).astype(float) if mot.any() else np.zeros(n)
    curve = (
        0.40 * _z(_smooth(loud, 1.0))
        + 0.25 * _z(_smooth(onset, 0.5))
        + 0.30 * _z(_smooth(mot, 1.0))
        + 0.05 * _z(_smooth(cuts, 2.0))
    )
    return curve


def find_candidates(
    path: Path,
    min_s: float = 12,
    max_s: float = 35,
    top_k: int = 6,
    lengths: tuple[float, ...] | None = None,
) -> list[Candidate]:
    curve = excitement_curve(path)
    n = len(curve)
    total_s = n / FPS
    if total_s <= min_s:
        return [Candidate(str(path), 0.0, total_s, float(curve.mean()), float(curve.argmax() / FPS))]

    lengths = lengths or tuple(sorted({min_s, (min_s + max_s) / 2, max_s}))
    scored: list[Candidate] = []
    step = max(1, FPS)  # evaluate a window start every second
    for length in lengths:
        w = int(length * FPS)
        if w > n:
            continue
        for start in range(0, n - w + 1, step):
            seg = curve[start : start + w]
            peak_i = int(seg.argmax())
            hook = seg[: int(3 * FPS)].max()  # best moment in the first 3 s
            sustained = seg.mean()
            tail = seg[-int(2 * FPS):].mean()  # ending on action helps loops/rewatch
            # Short clips get a mild bonus: shorts with higher completion rate do better.
            brevity = 1.0 - 0.15 * (length - min_s) / max(1e-9, max_s - min_s)
            score = (0.45 * hook + 0.35 * sustained + 0.20 * tail) * brevity
            scored.append(Candidate(str(path), start / FPS, (start + w) / FPS, float(score),
                                    (start + peak_i) / FPS))

    scored.sort(key=lambda c: c.score, reverse=True)
    picked: list[Candidate] = []
    for cand in scored:
        if all(cand.end_s <= p.start_s or cand.start_s >= p.end_s for p in picked):
            picked.append(cand)
        if len(picked) >= top_k:
            break
    return picked


def extract_frames(path: Path, start_s: float, end_s: float, n: int, out_dir: Path) -> list[Path]:
    """Grab ``n`` evenly spaced JPEG frames from a segment (for the vision model)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(n):
        t = start_s + (end_s - start_s) * (i + 0.5) / n
        p = out_dir / f"f{i:02d}.jpg"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(path),
             "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "4", str(p)],
            check=True,
        )
        paths.append(p)
    return paths
