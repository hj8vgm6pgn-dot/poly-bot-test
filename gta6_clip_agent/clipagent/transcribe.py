"""Speech-to-text with word timings, and the helpers built on it.

For reaction and stream footage the best moments are in what the creator *says*,
so the transcript drives three things:

  * moment selection - stretches where the creator is talking score higher, and
    Claude reads the words of each candidate
  * clean cuts       - clip edges snap to pauses and sentence ends
  * subtitles        - short word-timed cards burned into the clip

Uses faster-whisper locally (no API key). The model downloads on first use.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

from .config import settings

log = logging.getLogger(__name__)
Word = dict  # {"s": start seconds, "e": end seconds, "w": text}
SENTENCE_END = (".", "?", "!")


def transcribe(path: Path, cache: Path) -> list[Word] | None:
    """Word-level transcript of a video, cached as JSON. None if unavailable."""
    if cache.exists():
        return json.loads(cache.read_text())
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log.warning("faster-whisper is not installed; continuing without subtitles")
        return None
    try:
        model = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")
        segments, _info = model.transcribe(str(path), word_timestamps=True, vad_filter=True)
        words = [{"s": round(w.start, 2), "e": round(w.end, 2), "w": w.word.strip()}
                 for seg in segments for w in (seg.words or []) if w.word.strip()]
    except Exception as e:  # model download or decode failure should not stop the scan
        log.warning("transcription failed (%s); continuing without subtitles", e)
        return None
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(words))
    return words


def words_in(words: list[Word], start: float, end: float) -> list[Word]:
    return [w for w in words if w["s"] >= start - 0.05 and w["e"] <= end + 0.05]


def text_in(words: list[Word], start: float, end: float, with_times: bool = False) -> str:
    ws = words_in(words, start, end)
    if not with_times:
        return " ".join(w["w"] for w in ws)
    # One line per ~sentence with its offset from the window start, for the model.
    lines, cur, t0 = [], [], None
    for i, w in enumerate(ws):
        if t0 is None:
            t0 = w["s"] - start
        cur.append(w["w"])
        gap = ws[i + 1]["s"] - w["e"] if i + 1 < len(ws) else 99
        if w["w"].endswith(SENTENCE_END) or gap > 0.6:
            lines.append(f"[{max(0.0, t0):4.1f}s] {' '.join(cur)}")
            cur, t0 = [], None
    return "\n".join(lines)


def speech_rate(words: list[Word], n: int, fps: int) -> np.ndarray:
    """Words per second in each analysis bin."""
    rate = np.zeros(n)
    for w in words:
        i = int(w["s"] * fps)
        if 0 <= i < n:
            rate[i] += fps
    return rate


def _boundaries(words: list[Word], gap: float) -> tuple[list[float], list[float]]:
    """Times where a phrase can start, and times where one can end."""
    starts, ends = [], []
    for i, w in enumerate(words):
        prev = words[i - 1] if i else None
        if prev is None or w["s"] - prev["e"] >= gap or prev["w"].endswith(SENTENCE_END):
            starts.append(max(0.0, w["s"] - 0.12))
            if prev is not None:
                ends.append(prev["e"] + 0.18)
    if words:
        ends.append(words[-1]["e"] + 0.18)
    return starts, ends


def snap_to_speech(
    words: list[Word], start: float, end: float,
    max_shift: float = 2.5, gap: float = 0.35, min_len: float = 6.0,
) -> tuple[float, float]:
    """Move clip edges to the nearest pause or sentence end so nobody is cut off mid-word."""
    if not words:
        return start, end
    starts, ends = _boundaries(words, gap)
    s_opts = [t for t in starts if abs(t - start) <= max_shift]
    new_start = min(s_opts, key=lambda t: abs(t - start)) if s_opts else start
    e_opts = [t for t in ends if abs(t - end) <= max_shift and t - new_start >= min_len]
    new_end = min(e_opts, key=lambda t: abs(t - end)) if e_opts else end
    if new_end - new_start < min_len:
        return start, end
    return round(new_start, 2), round(new_end, 2)


def caption_cards(
    words: list[Word], start: float, end: float, max_words: int = 3, max_chars: int = 18,
) -> list[tuple[float, float, str]]:
    """Group words into short cards timed relative to the clip start."""
    cards, cur = [], []
    ws = words_in(words, start, end)

    def flush() -> None:
        if cur:
            cards.append((max(0.0, cur[0]["s"] - start), min(end - start, cur[-1]["e"] - start),
                          " ".join(w["w"] for w in cur)))
            cur.clear()

    for i, w in enumerate(ws):
        if cur and (len(cur) >= max_words or len(" ".join(x["w"] for x in cur)) + len(w["w"]) + 1 > max_chars):
            flush()
        cur.append(w)
        nxt = ws[i + 1] if i + 1 < len(ws) else None
        if w["w"].endswith(SENTENCE_END) or (nxt and nxt["s"] - w["e"] > 0.5):
            flush()
    flush()
    # Hold each card until the next one starts (up to a second) so captions do not flicker.
    out = []
    for i, (a, b, t) in enumerate(cards):
        nxt = cards[i + 1][0] if i + 1 < len(cards) else end - start
        out.append((round(a, 2), round(max(b, min(nxt, b + 1.0)), 2), t))
    return out


def _ass_time(t: float) -> str:
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def write_ass(cards: list[tuple[float, float, str]], path: Path, y: int = 1330,
              font: str = "DejaVu Sans", size: int = 72) -> Path:
    """Write subtitle cards as an ASS file sized for a 1080x1920 frame."""
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
        "Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
        f"Style: Sub,{font},{size},&H00FFFFFF,&H00000000,&H80000000,1,1,5,1,8,40,40,{y}\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Text\n"
    )
    lines = []
    for a, b, text in cards:
        clean = text.replace("{", "(").replace("}", ")").replace("\n", " ")
        lines.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},Sub,{clean}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return path
