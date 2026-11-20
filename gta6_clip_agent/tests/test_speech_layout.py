"""Transcript helpers, layout choice and the stacked render. No model downloads."""
import json
import subprocess
from pathlib import Path

import pytest

from clipagent import edit, layout, transcribe

FONT = __import__("clipagent.config", fromlist=["settings"]).settings.font_file


def W(s, e, w):
    return {"s": s, "e": e, "w": w}


WORDS = [
    W(10.0, 10.3, "Wait"), W(10.3, 10.6, "wait"), W(10.6, 11.0, "wait."),
    W(12.0, 12.3, "Look"), W(12.3, 12.5, "at"), W(12.5, 12.8, "the"), W(12.8, 13.2, "map"),
    W(13.2, 13.6, "size!"),
    W(15.0, 15.4, "That"), W(15.4, 15.7, "is"), W(15.7, 16.3, "insane."),
    W(21.0, 21.4, "Okay"), W(21.4, 21.9, "next."),
]


def test_snap_moves_edges_to_sentence_boundaries():
    # Window starts mid-sentence ("at the map...") and ends mid-word.
    start, end = transcribe.snap_to_speech(WORDS, 12.4, 16.0, min_len=3)
    assert start == pytest.approx(11.88, abs=0.01)   # just before "Look"
    assert end == pytest.approx(16.48, abs=0.01)     # just after "insane."


def test_snap_leaves_window_alone_without_nearby_boundary():
    assert transcribe.snap_to_speech([], 5, 20) == (5, 20)
    assert transcribe.snap_to_speech(WORDS, 40, 55) == (40, 55)


def test_caption_cards_are_short_and_relative():
    cards = transcribe.caption_cards(WORDS, 11.9, 16.5)
    assert [c[2] for c in cards][:2] == ["Look at the", "map size!"]
    assert all(len(c[2]) <= 18 for c in cards)
    assert cards[0][0] == pytest.approx(0.1, abs=0.05)
    assert all(a < b for a, b, _ in cards)


def test_transcript_text_for_the_model_has_offsets():
    text = transcribe.text_in(WORDS, 10, 17, with_times=True)
    assert "[ 0.0s] Wait wait wait." in text and "Look at the map size!" in text


def test_speech_rate_marks_talking():
    rate = transcribe.speech_rate(WORDS, n=100, fps=4)
    assert rate[40:66].sum() > 0 and rate[70:80].sum() == 0


def test_cached_transcript_is_reused(tmp_path):
    cache = tmp_path / "t.json"
    cache.write_text(json.dumps(WORDS))
    assert transcribe.transcribe(tmp_path / "missing.mp4", cache) == WORDS


def test_layout_choice():
    assert layout.choose(None) == "blur"
    assert layout.choose((0.05, 0.6, 0.12, 0.2)) == "stack"   # small face-cam in a corner
    assert layout.choose((0.3, 0.2, 0.4, 0.5)) == "crop"      # creator fills the frame


def test_stacked_render_with_subtitles(tmp_path):
    src = tmp_path / "in.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=14",
         "-f", "lavfi", "-i", "anoisesrc=d=14:a=0.3", "-shortest",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)], check=True)
    out = edit.render(src, 1, 13, "Kai reacts to the map size", tmp_path / "o.mp4", FONT, mode="stack",
                      face=(0.05, 0.6, 0.12, 0.22), credit="Clip: @creator",
                      captions=transcribe.caption_cards(WORDS, 10, 22))
    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
         "-of", "json", str(out)], capture_output=True, text=True, check=True).stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)
    assert not (tmp_path / "o.ass").exists()   # temp subtitle file is cleaned up


def test_no_face_in_synthetic_footage(tmp_path):
    src = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=10:d=4",
                    "-c:v", "libx264", "-preset", "ultrafast", str(src)], check=True)
    assert layout.detect_face(Path(src), 0, 4, samples=3) is None
