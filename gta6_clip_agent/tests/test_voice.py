"""Own-voice recordings: lookup, pitch shift, and precedence over text-to-speech."""
import subprocess

import numpy as np
import pytest

from clipagent import voice
from clipagent.config import settings


def _tone(path, hz=200, pad=True):
    af = ["-af", "adelay=800|800,apad=pad_dur=1"] if pad else []
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f={hz}:d=3", *af, str(path)],
                   check=True)
    return path


def _peak_hz(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1",
                          "-ar", "48000", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(float)
    return np.fft.rfftfreq(len(x), 1 / 48000)[np.abs(np.fft.rfft(x)).argmax()], len(x) / 48000


def test_recording_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "voice_dir", tmp_path)
    assert voice.recording_for(12) is None
    (tmp_path / "notes.txt").write_text("x")
    f = tmp_path / "clip_00012.m4a"
    f.write_bytes(b"x")
    assert voice.recording_for(12) == f
    assert voice.recording_for(13) is None
    (tmp_path / "13.MP3").write_bytes(b"x")
    assert voice.recording_for(13).name == "13.MP3"


def test_pitch_goes_deeper_and_silence_is_trimmed(tmp_path):
    src = _tone(tmp_path / "me.m4a")
    out = voice.process_recording(src, tmp_path / "out", semitones=-4)
    hz, dur = _peak_hz(out)
    assert hz == pytest.approx(200 * 2 ** (-4 / 12), rel=0.03)
    assert 2.7 < dur < 3.6          # 4.8s in, leading and trailing silence removed


def test_zero_pitch_leaves_voice_unchanged(tmp_path):
    out = voice.process_recording(_tone(tmp_path / "me.wav", pad=False), tmp_path / "out", semitones=0)
    assert _peak_hz(out)[0] == pytest.approx(200, rel=0.02)


def test_recording_wins_over_tts(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "voice_dir", tmp_path / "voice")
    monkeypatch.setattr(settings, "tts_provider", "none")
    (tmp_path / "voice").mkdir()
    path, origin = voice.voiceover_for(7, "A line.", tmp_path / "out")
    assert (path, origin) == (None, "none")
    _tone(tmp_path / "voice" / "clip_7.m4a")
    path, origin = voice.voiceover_for(7, "A line.", tmp_path / "out")
    assert origin == "recording" and path.exists()


def test_presets_change_character_without_changing_length(tmp_path):
    src = _tone(tmp_path / "me.wav", pad=False)
    hz, dur = _peak_hz(voice.process_recording(src, tmp_path / "a", preset="natural_low"))
    assert hz == pytest.approx(200 * 2 ** (-2 / 12), rel=0.03) and dur == pytest.approx(3.0, abs=0.3)
    # "different" keeps the pitch and only changes the tone of the voice.
    hz, _ = _peak_hz(voice.process_recording(src, tmp_path / "b", preset="different"))
    assert hz == pytest.approx(200, rel=0.03)
    with pytest.raises(RuntimeError, match="unknown voice preset"):
        voice.process_recording(src, tmp_path / "c", preset="robot")


def test_long_pauses_are_shortened_but_short_ones_kept():
    sr = 48000
    t = np.arange(sr) / sr
    tone = (np.sin(2 * np.pi * 200 * t) * 8000).astype(np.int16)
    gap = lambda s: np.zeros(int(sr * s), dtype=np.int16)
    pcm = np.concatenate([tone, gap(1.5), tone, gap(0.2), tone])
    out = voice._tighten_pauses(pcm, sr, max_pause_s=0.35)
    assert len(out) / sr == pytest.approx(3 + 0.35 + 0.2, abs=0.05)


def test_slow_reads_are_sped_up_within_limits(monkeypatch):
    monkeypatch.setattr(settings, "voice_target_wpm", 170)
    monkeypatch.setattr(settings, "voice_max_speedup", 1.12)
    assert voice.pace_speedup(20, 6.0) == 1.0          # 200 wpm: already quick
    assert voice.pace_speedup(20, 7.5) == pytest.approx(1.062, abs=0.01)   # 160 wpm
    assert voice.pace_speedup(20, 12.0) == 1.12        # 100 wpm: capped
    assert voice.pace_speedup(2, 5.0) == 1.0           # too short to judge
