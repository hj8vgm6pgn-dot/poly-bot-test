"""Permission gate, credit and voiceover tests. No network calls."""
import datetime as dt
import json
import subprocess
from pathlib import Path

import pytest

from clipagent import cli, edit, sources
from clipagent.config import settings
from clipagent.db import DB
from clipagent.publish.common import full_caption
from clipagent.sources import PermissionError_

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _src(**over):
    base = dict(id=1, name="Creator", platform="youtube", channel_id="UC_creator", handle="@creator",
                permission="direct", proof="email.png", credit="@creator", active=1, expires_at=None)
    return {**base, **over}


def test_gate_requires_source_proof_and_validity():
    assert sources.permission_problem(None)
    assert sources.permission_problem(_src()) is None
    assert "proof" in sources.permission_problem(_src(proof=""))
    assert "deactivated" in sources.permission_problem(_src(active=0))
    assert "expired" in sources.permission_problem(_src(expires_at="2026-01-01"), today=dt.date(2026, 11, 20))
    assert sources.permission_problem(_src(expires_at="2027-01-01"), today=dt.date(2026, 11, 20)) is None
    # Creative Commons needs no personal proof; the licence on the video is the permission.
    assert sources.permission_problem(_src(permission="creative_commons", proof="")) is None


def test_download_only_from_the_approved_channel():
    sources.authorize_download(_src(), {"channel_id": "UC_creator"})
    with pytest.raises(PermissionError_, match="not to approved source"):
        sources.authorize_download(_src(), {"channel_id": "UC_someone_else", "channel": "Other"})
    with pytest.raises(PermissionError_):
        sources.authorize_download(None, {"channel_id": "UC_creator"})


def test_creative_commons_checks_the_licence_on_the_video():
    cc = _src(permission="creative_commons", proof="")
    sources.authorize_download(cc, {"channel_id": "UC_creator",
                                    "license": "Creative Commons Attribution license (reuse allowed)"})
    with pytest.raises(PermissionError_, match="Creative Commons"):
        sources.authorize_download(cc, {"channel_id": "UC_creator", "license": "Standard YouTube License"})


def test_credit_lines():
    assert sources.credit_line(_src()) == "Credit: @creator"
    cc = sources.credit_line(_src(permission="creative_commons"),
                             {"title": "Vice City tour", "url": "https://youtu.be/x"})
    assert "Vice City tour" in cc and "CC BY" in cc and "https://youtu.be/x" in cc
    assert sources.watermark(_src()) == "Clip: @creator"
    caption = full_caption({"caption": "Wild.", "credit": "Credit: @creator", "hashtags": '["gta6", "#clip"]'})
    assert caption == "Wild.\n\nCredit: @creator\n\n#gta6 #clip"


@pytest.fixture(scope="module")
def footage(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("f") / "creator_vod.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=30",
         "-f", "lavfi", "-i", "anoisesrc=d=30:a=0.3", "-shortest",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(out)], check=True)
    return out


def test_render_with_credit_captions_and_voiceover(footage, tmp_path):
    vo = tmp_path / "vo.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=300:d=4", str(vo)], check=True)
    out = edit.render(footage, 2, 16, "Nobody expected this", tmp_path / "c.mp4", FONT,
                      credit="Clip: @creator",
                      commentary="This is the third time that bridge has done this. Has it happened to you?",
                      voiceover=vo)
    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
         "-of", "json", str(out)], capture_output=True, text=True, check=True).stdout)
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert abs(float(info["format"]["duration"]) - 14) < 0.5


def test_cli_flow_blocks_unapproved_footage(footage, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(settings, "footage_dir", tmp_path / "footage")
    monkeypatch.setattr(settings, "output_dir", tmp_path / "out")
    monkeypatch.setattr(settings, "db_path", tmp_path / "out" / "db.sqlite")
    monkeypatch.setattr(settings, "tts_provider", "none")
    monkeypatch.setattr(settings, "min_clip_s", 8.0)
    monkeypatch.setattr(settings, "max_clip_s", 12.0)
    monkeypatch.setattr(settings, "candidates_per_video", 1)

    # A direct-permission source needs proof.
    with pytest.raises(SystemExit, match="proof"):
        cli.main(["sources", "add", "streamer", "--platform", "twitch", "--permission", "direct"])
    cli.main(["sources", "add", "streamer", "--platform", "twitch", "--permission", "clipping_program",
              "--proof", "https://example.com/campaign", "--tags", "streamerclips", "--payout", "1.5"])

    # Stray footage with no source is ignored by scan.
    stray = settings.footage_dir / "random_download.mp4"
    stray.write_bytes(footage.read_bytes())
    cli.main(["scan", "--no-ai"])
    assert "not linked to an approved source" in capsys.readouterr().out
    db = DB(settings.db_path)
    assert db.clips() == []

    cli.main(["import", str(footage), "--source", "1"])
    cli.main(["scan", "--no-ai"])
    clip = db.clips("candidate")[0]
    assert clip["credit"] == "Credit: @streamer"
    assert "streamerclips" in json.loads(clip["hashtags"])

    cli.main(["render"])
    cli.main(["approve", str(clip["id"])])

    # Permission withdrawn after rendering: publishing is blocked.
    cli.main(["sources", "disable", "1"])
    capsys.readouterr()
    cli.main(["publish", "--dry-run"])
    assert "BLOCKED" in capsys.readouterr().out
    cli.main(["sources", "enable", "1"])
    cli.main(["publish", "--dry-run", "--platforms", "youtube"])
    assert "would post" in capsys.readouterr().out
