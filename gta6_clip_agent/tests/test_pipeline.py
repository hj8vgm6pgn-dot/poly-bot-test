"""End-to-end tests on a synthetic 60s video: calm for most of it, with a loud,
high-motion "action" burst at 30-38s. The analyzer should find it and the
renderer should produce a 1080x1920 file. No network or API calls."""
import json
import subprocess
from pathlib import Path

import pytest

from clipagent import analyze, edit
from clipagent.db import DB
from clipagent.score import ClipJudgement, _schema

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


@pytest.fixture(scope="module")
def video(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("v") / "gameplay.mp4"
    # Video: static gray, with a fast-moving test pattern between 30s and 38s.
    # Audio: quiet sine, with loud white noise between 30s and 38s.
    vf = (
        "[0:v][1:v]overlay=x='if(between(t,30,38),mod(t*900,1280)-640,-2000)':y=0:"
        "enable='between(t,30,38)'[v]"
    )
    af = (
        "[2:a]volume=0.05[quiet];"
        "[3:a]volume='if(between(t,30,38),0.9,0)':eval=frame[loud];"
        "[quiet][loud]amix=inputs=2:normalize=0[a]"
    )
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "color=c=gray:s=1280x720:r=30:d=60",
         "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=60",
         "-f", "lavfi", "-i", "sine=f=220:d=60",
         "-f", "lavfi", "-i", "anoisesrc=d=60:a=1",
         "-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(out)],
        check=True,
    )
    return out


def test_finds_action_burst(video):
    cands = analyze.find_candidates(video, min_s=12, max_s=20, top_k=3)
    assert cands, "expected at least one candidate"
    best = cands[0]
    assert best.start_s <= 31 and best.end_s >= 33, best
    assert 29 <= best.peak_s <= 39, best
    # Candidates must not overlap.
    for a in cands:
        for b in cands:
            if a is not b:
                assert a.end_s <= b.start_s or a.start_s >= b.end_s


def test_render_vertical(video, tmp_path):
    out = edit.render(video, 28, 40, "He did NOT see that coming: 100% real", tmp_path / "c.mp4", FONT)
    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
         "-of", "json", str(out)], capture_output=True, text=True, check=True).stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert abs(float(info["format"]["duration"]) - 12) < 0.5


def test_extract_frames(video, tmp_path):
    frames = analyze.extract_frames(video, 30, 38, 4, tmp_path)
    assert len(frames) == 4 and all(f.stat().st_size > 0 for f in frames)


def test_judgement_schema_is_strict():
    s = _schema()
    assert s["additionalProperties"] is False
    assert set(s["required"]) == set(ClipJudgement.model_fields)


def test_db_roundtrip(tmp_path):
    db = DB(tmp_path / "t.db")
    cid = db.add_clip(source="x.mp4", start_s=1, end_s=10, signal_score=1.2, ai_score=77,
                      hook="h", title="t", caption="c", hashtags='["gta6"]')
    db.update_clip(cid, status="approved")
    assert db.clips("approved")[0]["id"] == cid
    db.add_post(cid, "youtube", "abc", "posted")
    db.update_post_metrics("youtube", "abc", {"views": 1000, "likes": 50, "comments": 3, "shares": None})
    assert db.best_posts()[0]["views"] == 1000


def test_judge_parses_structured_output(video, tmp_path):
    from types import SimpleNamespace

    from clipagent.score import Judge

    payload = {
        "virality_score": 82, "keep": True, "category": "chase", "hook_text": "Cops had no chance",
        "title": "Vice City chase gone wrong", "caption": "Would you have made it?",
        "hashtags": ["gta6", "#gtavi"], "trim_start_offset_s": 1.0, "trim_end_offset_s": 0.5,
        "reasoning": "Strong opening crash.",
    }
    calls = {}

    class FakeMessages:
        def create(self, **kw):
            calls.update(kw)
            return SimpleNamespace(stop_reason="end_turn",
                                   content=[SimpleNamespace(type="text", text=json.dumps(payload))])

    fake = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages()))
    frames = analyze.extract_frames(video, 30, 38, 2, tmp_path)
    j = Judge(client=fake).judge(frames, 8.0, 1.5, "context")
    assert j.virality_score == 82 and j.category == "chase"
    assert calls["output_config"]["format"]["type"] == "json_schema"
    assert sum(1 for b in calls["messages"][0]["content"] if b["type"] == "image") == 2


def test_compile_over_one_minute(video, tmp_path):
    segs = [(video, 0, 25, True), (video, 28, 50, True), (video, 40, 58, True)]
    out = edit.compile_segments(segs, "Three wild moments", tmp_path / "comp.mp4", FONT)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout)
    assert dur > 60


def test_accounts_report_unconfigured(monkeypatch):
    from clipagent import accounts
    from clipagent.config import settings

    for k in ("youtube_client_id", "youtube_refresh_token", "ig_user_id", "ig_access_token",
              "tiktok_access_token"):
        monkeypatch.setattr(settings, k, None)
    for st in accounts.all_statuses():
        assert not st.connected and st.error
        assert "NOT CONNECTED" in accounts.format_status(st)
