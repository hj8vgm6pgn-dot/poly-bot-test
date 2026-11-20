"""The commentary track: your own recorded voice, or text-to-speech as a fallback.

Your own voice is the strongest "original input" a clips channel can add. Record
the line the agent wrote (`python -m clipagent script` lists them), save it as
voice/clip_<id>.m4a, and the agent cleans it up, lowers the pitch by VOICE_PITCH
semitones and mixes it in. A recording always wins over text-to-speech.

Text-to-speech providers, used only when there is no recording:

The commentary is what makes a clip of someone else's footage *your* video in the
eyes of the platforms' reused-content rules, so every clip gets a short original
line written by Claude. Providers, in order of preference:

  elevenlabs  - natural voices, needs ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID
  piper       - free local neural voices, needs the `piper` binary and PIPER_MODEL
  espeak      - free and robotic; a last resort

If none is available the commentary is still burned in as on-screen captions.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import requests

from .config import settings

log = logging.getLogger(__name__)


AUDIO_EXT = (".m4a", ".mp3", ".wav", ".aac", ".ogg", ".opus", ".caf", ".flac", ".mov", ".mp4")


def recording_for(clip_id: int) -> Path | None:
    """Your recording for a clip: voice/clip_00012.m4a, voice/clip_12.m4a or voice/12.m4a."""
    d = settings.voice_dir
    if not d.is_dir():
        return None
    names = {f"clip_{clip_id:05d}", f"clip_{clip_id}", str(clip_id)}
    for p in sorted(d.iterdir()):
        if p.stem.lower() in names and p.suffix.lower() in AUDIO_EXT:
            return p
    return None


def _shift(pitch_st: float = 0.0, formant_st: float = 0.0) -> str:
    """Filter chain that moves pitch and voice character (formants) independently.

    Pitch is how high or low the voice is. Formants are what make it sound like a
    particular person. Stage one moves both together by ``formant_st``; stage two
    moves pitch alone to reach ``pitch_st``.
    """
    stages = []
    if abs(formant_st) > 0.01:
        stages.append(f"rubberband=pitch={2 ** (formant_st / 12):.5f}:formant=shifted:pitchq=quality")
    rest = pitch_st - formant_st
    if abs(rest) > 0.01:
        stages.append(f"rubberband=pitch={2 ** (rest / 12):.5f}:formant=preserved:pitchq=quality")
    return ",".join(stages) or "anull"


# name: (description, filter chain)
PRESETS: dict[str, tuple[str, str]] = {
    "natural_low": ("Your own voice, a little lower. Character unchanged.",
                    _shift(-2.0, 0.0)),
    "warm": ("Slightly lower and fuller, with a touch of bass.",
             _shift(-1.5, -1.5) + ",bass=g=3:f=140"),
    "narrator": ("Lower, with a broadcast-style tone: more bass and more clarity.",
                 _shift(-2.5, -1.0) + ",bass=g=4:f=120,equalizer=f=3500:t=q:w=1:g=3"),
    "different": ("Same pitch as you, but a darker tone, so it sounds less like you.",
                  _shift(0.0, -2.5)),
    "light": ("A little higher and brighter.",
              _shift(1.5, 1.5)),
}


def _trim_silence(pcm: "np.ndarray", sr: int, threshold_db: float = -45.0,
                  lead_s: float = 0.05, tail_s: float = 0.15) -> "np.ndarray":
    """Drop silence before the first and after the last audible 20 ms frame."""
    import numpy as np

    hop = sr // 50
    n = len(pcm) // hop
    if n == 0:
        return pcm
    frames = pcm[: n * hop].reshape(n, hop).astype(np.float64) / 32768.0
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    # Judge silence against how loud this recording's speech is, so quiet takes are not cut.
    threshold_db = min(threshold_db, np.percentile(db, 95) - 30)
    loud = np.flatnonzero(db > threshold_db)
    if len(loud) == 0:
        return pcm
    start = max(0, int(loud[0] * hop - lead_s * sr))
    end = min(len(pcm), int((loud[-1] + 1) * hop + tail_s * sr))
    return pcm[start:end]


def _tighten_pauses(pcm: "np.ndarray", sr: int, max_pause_s: float, threshold_db: float = -42.0) -> "np.ndarray":
    """Shorten every silent gap longer than ``max_pause_s`` to that length.

    Dead air is where viewers swipe away. Short pauses stay, so the read keeps its rhythm.
    """
    import numpy as np

    hop = sr // 100  # 10 ms
    n = len(pcm) // hop
    if n == 0 or max_pause_s <= 0:
        return pcm
    frames = pcm[: n * hop].reshape(n, hop).astype(np.float64) / 32768.0
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    # Relative to this recording's speech level, so soft syllables in a quiet take survive.
    quiet = db < min(threshold_db, np.percentile(db, 95) - 30)
    keep = np.ones(n, dtype=bool)
    limit = int(max_pause_s * 100)
    i = 0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if j - i > limit:
                # keep half the allowed pause on each side so word edges are not clipped
                keep[i + limit // 2 : j - (limit - limit // 2)] = False
            i = j
        else:
            i += 1
    return pcm[: n * hop].reshape(n, hop)[keep].reshape(-1) if keep.any() else pcm


def pace_speedup(word_count: int, duration_s: float) -> float:
    """Tempo factor that brings a slow read up toward the target pace (1.0 = leave alone)."""
    if word_count < 4 or duration_s <= 0:
        return 1.0
    wpm = word_count / duration_s * 60
    if wpm >= settings.voice_target_wpm:
        return 1.0
    return round(min(settings.voice_max_speedup, settings.voice_target_wpm / wpm), 3)


def process_recording(src: Path, out_path: Path, semitones: float | None = None,
                      preset: str | None = None, script: str = "") -> Path:
    """Clean up a voice recording and apply the voice effect.

    The effect is a named preset (``preset`` or VOICE_PRESET) if one is set, otherwise
    a plain pitch shift of ``semitones`` / VOICE_PITCH (negative = deeper).

    Steps: cut rumble and background hiss, trim silence at both ends, shift the pitch
    without changing speed, even out the level.
    """
    import numpy as np

    sr = 48000
    st = settings.voice_pitch if semitones is None else semitones
    ratio = 2 ** (st / 12)
    out = out_path.with_suffix(".wav")
    out.parent.mkdir(parents=True, exist_ok=True)

    dec = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(sr),
         "-af", "highpass=f=70,afftdn=nr=10", "-f", "s16le", "-"],
        capture_output=True, timeout=180,
    )
    if dec.returncode != 0 or len(dec.stdout) < sr // 5:
        raise RuntimeError(f"could not read recording {src}: {dec.stderr.decode(errors='replace')[-300:]}")
    pcm = _trim_silence(np.frombuffer(dec.stdout[: len(dec.stdout) // 2 * 2], dtype=np.int16), sr)
    pcm = _tighten_pauses(pcm, sr, settings.voice_max_pause)
    tempo = pace_speedup(len(script.split()), len(pcm) / sr)

    # Light compression only: the rise and fall in loudness is part of what keeps a voice
    # interesting, so it is evened out, not flattened.
    level = "acompressor=threshold=-18dB:ratio=2:attack=8:release=150,loudnorm=I=-16:TP=-1.5:LRA=11"
    if tempo > 1.001:
        level = f"atempo={tempo:.3f},{level}"
    shifters = [f"rubberband=pitch={ratio:.5f}",
                # Fallback without librubberband: resample, then restore the speed.
                f"asetrate={sr}*{ratio:.5f},aresample={sr},atempo={1 / ratio:.5f}"]
    if abs(st) < 0.01:
        shifters = ["anull"]
    name = preset or (settings.voice_preset if semitones is None else None)
    if name:
        if name not in PRESETS:
            raise RuntimeError(f"unknown voice preset '{name}'; choose from: {', '.join(PRESETS)}")
        shifters = [PRESETS[name][1]]
    last = ""
    for shift in shifters:
        try:
            run = subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "s16le", "-ar", str(sr), "-ac", "1",
                 "-i", "-", "-af", f"{shift},{level}", "-ar", str(sr), str(out)],
                input=pcm.tobytes(), capture_output=True, timeout=180,
            )
        except subprocess.TimeoutExpired:
            last = "timed out"
            continue
        if run.returncode == 0 and out.exists() and out.stat().st_size > 1000:
            return out
        last = run.stderr.decode(errors="replace")[-300:]
    raise RuntimeError(f"could not process recording {src}: {last}")


def voiceover_for(clip_id: int, text: str, out_dir: Path) -> tuple[Path | None, str]:
    """The voiceover file for a clip and where it came from: recording | tts | none."""
    rec = recording_for(clip_id)
    if rec:
        try:
            return process_recording(rec, out_dir / f"vo_{clip_id:05d}", script=text), "recording"
        except RuntimeError as e:
            log.warning("%s - falling back", e)
    vo = synthesize(text, out_dir / f"vo_{clip_id:05d}")
    return vo, ("tts" if vo else "none")


def available_provider() -> str | None:
    want = settings.tts_provider
    if want == "none":
        return None
    checks = {
        "elevenlabs": bool(settings.elevenlabs_api_key and settings.elevenlabs_voice_id),
        "piper": bool(shutil.which("piper") and settings.piper_model),
        "espeak": bool(shutil.which("espeak-ng")),
    }
    if want != "auto":
        return want if checks.get(want) else None
    return next((p for p in ("elevenlabs", "piper", "espeak") if checks[p]), None)


def synthesize(text: str, out_path: Path) -> Path | None:
    """Write speech for ``text`` to ``out_path`` (extension set per provider). None if unavailable."""
    text = " ".join(text.split())
    provider = available_provider()
    if not text or not provider:
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if provider == "elevenlabs":
            out = out_path.with_suffix(".mp3")
            r = requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}",
                headers={"xi-api-key": settings.elevenlabs_api_key, "Content-Type": "application/json"},
                json={"text": text, "model_id": settings.elevenlabs_model},
                timeout=120,
            )
            r.raise_for_status()
            out.write_bytes(r.content)
            return out
        out = out_path.with_suffix(".wav")
        if provider == "piper":
            subprocess.run(["piper", "--model", settings.piper_model, "--output_file", str(out)],
                           input=text, text=True, check=True, capture_output=True)
        else:
            subprocess.run(["espeak-ng", "-s", "165", "-w", str(out), text], check=True, capture_output=True)
        return out
    except (requests.RequestException, subprocess.CalledProcessError, OSError) as e:
        log.warning("voiceover failed (%s): %s - falling back to captions only", provider, e)
        return None
