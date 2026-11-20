"""Approved sources: the only places footage is allowed to come from.

A *source* is a creator channel plus the reason you are allowed to clip it:

  clipping_program  - the creator runs a clipping campaign (Whop, Discord, their site)
  direct            - the creator told you in writing that you may clip them
  creative_commons  - the specific video is published under YouTube's CC BY licence
  own               - your own recordings

Every download, scan and publish goes through ``permission_problem``. Nothing is
fetched from a channel that is not on the list, and a clipping/direct source must
have proof on file (a link or file path to the campaign page, email or DM).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
import subprocess
from pathlib import Path

import requests

from .config import settings

log = logging.getLogger(__name__)
TIMEOUT = 30
YT = "https://www.googleapis.com/youtube/v3"
PERMISSIONS = ("clipping_program", "direct", "creative_commons", "own")
CC_MARKERS = ("creative commons", "creativecommon")


class PermissionError_(RuntimeError):
    """Raised when footage is not covered by an approved, valid permission."""


# --------------------------------------------------------------------------- gate
def permission_problem(source: dict | None, today: dt.date | None = None) -> str | None:
    """Return why this source may NOT be used, or None if it is fine."""
    if source is None:
        return "no approved source is attached to this footage"
    if not source.get("active"):
        return f"source '{source['name']}' is deactivated"
    if source["permission"] not in PERMISSIONS:
        return f"unknown permission type '{source['permission']}'"
    if source["permission"] in ("clipping_program", "direct") and not (source.get("proof") or "").strip():
        return f"source '{source['name']}' has no proof of permission on file"
    exp = source.get("expires_at")
    if exp:
        try:
            if dt.date.fromisoformat(exp[:10]) < (today or dt.date.today()):
                return f"permission from '{source['name']}' expired on {exp[:10]}"
        except ValueError:
            return f"source '{source['name']}' has an unreadable expiry date: {exp}"
    return None


def require(source: dict | None) -> None:
    problem = permission_problem(source)
    if problem:
        raise PermissionError_(problem)


def credit_line(source: dict, video: dict | None = None) -> str:
    """Caption credit. CC BY needs title, author, licence and a link."""
    who = source.get("credit") or source.get("handle") or source.get("name")
    if source["permission"] == "creative_commons" and video:
        return f"Clip from \"{video.get('title', '')}\" by {who} (CC BY) {video.get('url', '')}".strip()
    if source["permission"] == "own":
        return ""
    return f"Credit: {who}"


def watermark(source: dict) -> str:
    if source["permission"] == "own":
        return ""
    return f"Clip: {source.get('credit') or source.get('handle') or source.get('name')}"


# --------------------------------------------------------------------------- YouTube lookups
def _yt(path: str, **params) -> dict:
    if not settings.youtube_api_key:
        raise RuntimeError("YOUTUBE_API_KEY is required for this command")
    r = requests.get(f"{YT}/{path}", params={"key": settings.youtube_api_key, **params}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def resolve_youtube_channel(ref: str) -> dict:
    """Accepts @handle, a channel URL, or a UC... channel id."""
    ref = ref.strip().rstrip("/")
    if "youtube.com/" in ref:
        parts = [p for p in ref.split("youtube.com/")[1].split("?")[0].split("/") if p]
        ref = parts[1] if parts[0] == "channel" and len(parts) > 1 else parts[0]
    params = {"id": ref} if ref.startswith("UC") and len(ref) == 24 else {"forHandle": ref if ref.startswith("@") else f"@{ref}"}
    items = _yt("channels", part="snippet,contentDetails", **params).get("items", [])
    if not items:
        raise ValueError(f"No YouTube channel found for '{ref}'")
    ch = items[0]
    return {
        "channel_id": ch["id"],
        "name": ch["snippet"]["title"],
        "handle": ch["snippet"].get("customUrl") or ref,
        "uploads": ch["contentDetails"]["relatedPlaylists"]["uploads"],
    }


def _parse_iso_duration(s: str) -> float:
    # PT#H#M#S
    import re

    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0.0
    d, h, mi, sec = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + sec


def _video_details(ids: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(ids), 50):
        data = _yt("videos", part="snippet,statistics,status,contentDetails", id=",".join(ids[i:i + 50]))
        out += data.get("items", [])
    return out


def _matches_keywords(snippet: dict) -> bool:
    text = " ".join([snippet.get("title", ""), snippet.get("description", "")[:500],
                     " ".join(snippet.get("tags", []))]).lower()
    return any(k.strip().lower() in text for k in settings.source_keywords if k.strip())


def discover_channel(db, source: dict, max_videos: int = 30) -> int:
    """List an approved channel's recent GTA 6 uploads into the videos table."""
    require(source)
    uploads = "UU" + source["channel_id"][2:]
    items = _yt("playlistItems", part="contentDetails", playlistId=uploads,
                maxResults=min(50, max_videos)).get("items", [])
    ids = [i["contentDetails"]["videoId"] for i in items]
    n = 0
    for v in _video_details(ids):
        if not _matches_keywords(v["snippet"]):
            continue
        db.upsert_video(
            source_id=source["id"], platform="youtube", external_id=v["id"],
            url=f"https://www.youtube.com/watch?v={v['id']}", title=v["snippet"]["title"],
            license=v.get("status", {}).get("license", "youtube"),
            views=int(v.get("statistics", {}).get("viewCount", 0)),
            published_at=v["snippet"]["publishedAt"],
            duration_s=_parse_iso_duration(v.get("contentDetails", {}).get("duration", "")),
        )
        n += 1
    return n


def discover_creative_commons(db, query: str, days: int = 30, max_results: int = 25) -> int:
    """Find GTA 6 videos their uploaders published under the CC BY licence."""
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    found = _yt("search", part="id", q=query, type="video", videoLicense="creativeCommon",
                order="viewCount", publishedAfter=since, maxResults=max_results).get("items", [])
    ids = [i["id"]["videoId"] for i in found]
    n = 0
    for v in _video_details(ids):
        # Re-check the licence on the video itself; search filters are not a guarantee.
        if v.get("status", {}).get("license") != "creativeCommon":
            continue
        sn = v["snippet"]
        src = db.source_by_channel("youtube", sn["channelId"])
        if src is None:
            sid = db.add_source(
                name=sn["channelTitle"], platform="youtube", channel_id=sn["channelId"],
                handle=sn["channelTitle"], permission="creative_commons",
                proof=f"https://www.youtube.com/watch?v={v['id']}", credit=sn["channelTitle"],
                required_tags="[]",
            )
        else:
            sid = src["id"]
        db.upsert_video(
            source_id=sid, platform="youtube", external_id=v["id"],
            url=f"https://www.youtube.com/watch?v={v['id']}", title=sn["title"],
            license="creativeCommon", views=int(v.get("statistics", {}).get("viewCount", 0)),
            published_at=sn["publishedAt"],
            duration_s=_parse_iso_duration(v.get("contentDetails", {}).get("duration", "")),
        )
        n += 1
    return n


# --------------------------------------------------------------------------- Twitch
def discover_twitch_clips(db, source: dict, period: str = "7d", limit: int = 40) -> int:
    """List an approved Twitch channel's most-viewed clips from the last day/week/month.

    Viewers have already picked these moments by watching them, so they are the best
    guide to what works. Each one is a short clip of the creator's own stream. No Twitch
    login is needed; the listing is public.
    """
    require(source)
    if period not in ("24hr", "7d", "30d", "all"):
        raise ValueError("period must be 24hr, 7d, 30d or all")
    if not shutil.which("yt-dlp"):
        raise RuntimeError("yt-dlp is not installed (pip install yt-dlp)")
    login = str(source["channel_id"]).lstrip("@")
    out = subprocess.run(
        ["yt-dlp", "-J", "--flat-playlist", "--playlist-end", str(limit),
         f"https://www.twitch.tv/{login}/clips?filter=clips&range={period}"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"could not list Twitch clips: {out.stderr.strip()[-300:]}")
    n = 0
    for e in json.loads(out.stdout).get("entries") or []:
        if not e or not e.get("id"):
            continue
        ts = e.get("timestamp")
        db.upsert_video(
            source_id=source["id"], platform="twitch", external_id=str(e["id"]),
            url=e.get("url") or e.get("webpage_url") or f"https://clips.twitch.tv/{e['id']}",
            title=e.get("title") or "", license="twitch-clip", views=int(e.get("view_count") or 0),
            published_at=dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat() if ts else "",
            duration_s=e.get("duration"),
        )
        n += 1
    return n


# --------------------------------------------------------------------------- download
def _ytdlp_metadata(url: str) -> dict:
    if not shutil.which("yt-dlp"):
        raise RuntimeError("yt-dlp is not installed (pip install yt-dlp)")
    out = subprocess.run(["yt-dlp", "-J", "--no-playlist", url], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"could not read video metadata: {out.stderr.strip()[-300:]}")
    return json.loads(out.stdout)


def authorize_download(source: dict | None, meta: dict) -> None:
    """Check the *actual* video against the approved source before fetching it.

    The channel that really owns the video must be the approved channel, and a
    Creative Commons source only covers videos that carry the CC licence.
    """
    require(source)
    if str(meta.get("extractor", "")).lower().startswith("twitch"):
        # On a Twitch clip the "uploader" is whoever made the clip; the stream's owner is the
        # channel / creator. Only the owner counts.
        owners = [meta.get("channel"), meta.get("creator"), meta.get("channel_id"), meta.get("uploader_id")
                  if "clips" not in str(meta.get("extractor", "")).lower() else None]
    else:
        owners = [meta.get("channel_id") or meta.get("uploader_id")]
    owners = {str(o).lower().lstrip("@") for o in owners if o}
    if str(source["channel_id"]).lower().lstrip("@") not in owners:
        raise PermissionError_(
            f"video belongs to channel '{meta.get('channel') or meta.get('channel_id') or '?'}', "
            f"not to approved source '{source['name']}'"
        )
    if source["permission"] == "creative_commons":
        lic = (meta.get("license") or "").lower()
        if not any(m in lic for m in CC_MARKERS):
            raise PermissionError_("video is no longer marked Creative Commons")


def fetch(db, video: dict, max_minutes: float = 90) -> Path:
    """Download one discovered video from an approved source into footage/."""
    source = db.source(video["source_id"])
    require(source)
    if video.get("duration_s") and video["duration_s"] > max_minutes * 60:
        raise RuntimeError(f"video is {video['duration_s'] / 60:.0f} min long (limit {max_minutes:.0f}); "
                           "raise --max-minutes to fetch it")
    meta = _ytdlp_metadata(video["url"])
    authorize_download(source, meta)
    dest_dir = settings.footage_dir / f"source_{source['id']}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{video['external_id']}.mp4"
    run = subprocess.run(
        ["yt-dlp", "--no-playlist", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
         "--merge-output-format", "mp4", "-o", str(dest), video["url"]],
        capture_output=True, text=True,
    )
    if run.returncode != 0 or not dest.exists():
        raise RuntimeError(f"download failed: {run.stderr.strip()[-300:]}")
    db.update_video(video["id"], local_path=str(dest), status="downloaded")
    return dest


def import_file(db, path: Path, source: dict, title: str = "", url: str = "") -> int:
    """Register footage a creator sent you directly (Drive link, Discord upload, etc.)."""
    require(source)
    if not path.exists():
        raise FileNotFoundError(path)
    return db.upsert_video(
        source_id=source["id"], platform="file", external_id=str(path.resolve()), url=url,
        title=title or path.stem, license=source["permission"], views=0, published_at="",
        duration_s=None, local_path=str(path), status="downloaded",
    )
