"""Check that each platform account is connected and track progress toward monetization.

Thresholds below are the publicly stated requirements as of late 2026. They change and
vary by country, so always confirm in each app (YouTube Studio > Earn, TikTok Studio >
Monetization, Instagram Professional dashboard > Monetization).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import requests

from .config import settings

TIMEOUT = 30


@dataclass
class Status:
    platform: str
    connected: bool
    account: str = ""
    metrics: dict = field(default_factory=dict)
    goals: list[tuple[str, float, float]] = field(default_factory=list)  # (label, current, target)
    notes: list[str] = field(default_factory=list)
    error: str = ""


# --------------------------------------------------------------------------- YouTube
def youtube_status() -> Status:
    st = Status("youtube", False)
    if not (settings.youtube_client_id and settings.youtube_refresh_token):
        st.error = "OAuth not configured (YOUTUBE_CLIENT_ID / _SECRET / _REFRESH_TOKEN)"
        return st
    from .publish.youtube import _access_token

    try:
        token = _access_token()
        h = {"Authorization": f"Bearer {token}"}
        r = requests.get("https://www.googleapis.com/youtube/v3/channels",
                         params={"part": "snippet,statistics", "mine": "true"}, headers=h, timeout=TIMEOUT)
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            st.error = "token works but no channel on this Google account - create one in YouTube Studio"
            return st
        ch = items[0]
        st.connected, st.account = True, ch["snippet"]["title"]
        subs = int(ch["statistics"].get("subscriberCount", 0))
        st.metrics["subscribers"] = subs

        shorts_views_90d = _youtube_shorts_views(h, days=90)
        if shorts_views_90d is not None:
            st.metrics["shorts_views_90d"] = shorts_views_90d
        st.goals = [
            ("YPP (full): subscribers", subs, 1_000),
            ("YPP (full): Shorts views / 90 days", shorts_views_90d or 0, 10_000_000),
            ("YPP (fan funding tier): subscribers", subs, 500),
            ("YPP (fan funding tier): Shorts views / 90 days", shorts_views_90d or 0, 3_000_000),
        ]
        if shorts_views_90d is None:
            st.notes.append("Shorts views need the yt-analytics.readonly scope on the refresh token")
    except requests.HTTPError as e:
        st.error = f"API error: {e}"
    return st


def _youtube_shorts_views(headers: dict, days: int) -> int | None:
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    r = requests.get(
        "https://youtubeanalytics.googleapis.com/v2/reports",
        params={"ids": "channel==MINE", "startDate": start.isoformat(), "endDate": end.isoformat(),
                "metrics": "views", "dimensions": "creatorContentType"},
        headers=headers, timeout=TIMEOUT,
    )
    if r.status_code != 200:
        return None
    for row in r.json().get("rows", []):
        if str(row[0]).lower() == "shorts":
            return int(row[1])
    return 0


# --------------------------------------------------------------------------- Instagram
def instagram_status() -> Status:
    st = Status("instagram", False)
    if not (settings.ig_user_id and settings.ig_access_token):
        st.error = "not configured (IG_USER_ID / IG_ACCESS_TOKEN)"
        return st
    try:
        r = requests.get(f"https://graph.facebook.com/v21.0/{settings.ig_user_id}",
                         params={"access_token": settings.ig_access_token,
                                 "fields": "username,followers_count,media_count"},
                         timeout=TIMEOUT)
        r.raise_for_status()
        d = r.json()
        st.connected, st.account = True, "@" + d.get("username", "?")
        followers = int(d.get("followers_count", 0))
        st.metrics = {"followers": followers, "posts": d.get("media_count", 0)}
        st.goals = [("Subscriptions (typical requirement): followers", followers, 10_000)]
        st.notes.append("Instagram monetization is country- and invite-dependent; "
                        "check Professional dashboard > Monetization status")
        if not settings.public_video_base_url:
            st.notes.append("PUBLIC_VIDEO_BASE_URL not set - Reels publishing will fail")
    except requests.HTTPError as e:
        st.error = f"API error: {e}"
    return st


# --------------------------------------------------------------------------- TikTok
def tiktok_status() -> Status:
    st = Status("tiktok", False)
    from . import tiktok_auth

    try:
        token = tiktok_auth.access_token()
    except (RuntimeError, requests.RequestException) as e:
        st.error = f"sign-in expired or failed ({e}); run `tiktok-login` again"
        return st
    if not token:
        st.error = "not signed in (set TIKTOK_CLIENT_KEY/SECRET, then run `tiktok-login`)"
        return st
    h = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get("https://open.tiktokapis.com/v2/user/info/",
                         params={"fields": "display_name,follower_count,video_count,likes_count"},
                         headers=h, timeout=TIMEOUT)
        r.raise_for_status()
        u = r.json().get("data", {}).get("user", {})
        st.connected, st.account = True, u.get("display_name", "?")
        followers = int(u.get("follower_count", 0))
        views_30d = _tiktok_recent_views(h, days=30)
        st.metrics = {"followers": followers, "videos": u.get("video_count", 0),
                      "views_on_videos_from_last_30d": views_30d}
        st.goals = [
            ("Creator Rewards: followers", followers, 10_000),
            ("Creator Rewards: views / 30 days (approx.)", views_30d, 100_000),
        ]
        st.notes.append("Creator Rewards only pays on original videos longer than 1 minute "
                        "(use `compile` to build 60s+ cuts); must be 18+ in an eligible country")
    except requests.HTTPError as e:
        st.error = f"API error: {e} (token needs user.info.basic + user.info.stats + video.list scopes)"
    return st


def _tiktok_recent_views(headers: dict, days: int) -> int:
    cutoff = dt.datetime.now(dt.timezone.utc).timestamp() - days * 86400
    total, cursor = 0, None
    for _ in range(10):  # up to 200 videos
        body = {"max_count": 20}
        if cursor:
            body["cursor"] = cursor
        r = requests.post("https://open.tiktokapis.com/v2/video/list/",
                          params={"fields": "id,create_time,view_count"},
                          headers=headers, json=body, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json().get("data", {})
        vids = data.get("videos", [])
        for v in vids:
            if v.get("create_time", 0) >= cutoff:
                total += int(v.get("view_count", 0))
        if not data.get("has_more") or (vids and vids[-1].get("create_time", 0) < cutoff):
            break
        cursor = data.get("cursor")
    return total


def all_statuses() -> list[Status]:
    return [youtube_status(), tiktok_status(), instagram_status()]


def format_status(st: Status) -> str:
    lines = [f"== {st.platform.upper()} =="]
    if not st.connected:
        lines.append(f"  NOT CONNECTED: {st.error}")
        return "\n".join(lines)
    lines.append(f"  account: {st.account}")
    for k, v in st.metrics.items():
        lines.append(f"  {k}: {v:,}" if isinstance(v, int) else f"  {k}: {v}")
    for label, cur, target in st.goals:
        pct = min(100.0, 100.0 * cur / target) if target else 100.0
        bar = "#" * int(pct / 5) + "." * (20 - int(pct / 5))
        lines.append(f"  [{bar}] {pct:5.1f}%  {label}: {int(cur):,} / {int(target):,}")
    for n in st.notes:
        lines.append(f"  note: {n}")
    return "\n".join(lines)
