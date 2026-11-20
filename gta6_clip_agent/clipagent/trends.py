"""Trend research and own-account analytics via the platforms' official APIs.

What each platform lets you read (with your own credentials):
  * YouTube Data API v3  - public search + statistics for any video (title, views, tags).
  * Instagram Graph API  - top media for a hashtag (Business/Creator account) and your own
                           media insights.
  * TikTok Display API   - your *own* videos and their view/like/share counts. Public TikTok
                           search is only available through the Research API, which is
                           restricted to approved researchers.

We only read metadata (titles, hashtags, counts) to learn what topics are hot.
Videos are never downloaded here - the footage you edit is your own capture.
"""
from __future__ import annotations

import datetime as dt
import logging

import requests

from .config import settings

log = logging.getLogger(__name__)
TIMEOUT = 30


# --------------------------------------------------------------------------- YouTube
def youtube_trending_shorts(query: str, days: int = 7, max_results: int = 25) -> list[dict]:
    if not settings.youtube_api_key:
        log.info("YOUTUBE_API_KEY not set; skipping YouTube trends")
        return []
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    search = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "key": settings.youtube_api_key, "part": "snippet", "q": f"{query} #shorts",
            "type": "video", "videoDuration": "short", "order": "viewCount",
            "publishedAfter": since, "maxResults": max_results,
        },
        timeout=TIMEOUT,
    )
    search.raise_for_status()
    ids = [i["id"]["videoId"] for i in search.json().get("items", [])]
    if not ids:
        return []
    stats = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"key": settings.youtube_api_key, "part": "snippet,statistics", "id": ",".join(ids)},
        timeout=TIMEOUT,
    )
    stats.raise_for_status()
    out = []
    for v in stats.json().get("items", []):
        s, sn = v.get("statistics", {}), v["snippet"]
        out.append({
            "id": v["id"], "title": sn["title"], "published_at": sn["publishedAt"],
            "tags": sn.get("tags", []), "views": int(s.get("viewCount", 0)),
            "likes": int(s.get("likeCount", 0)), "comments": int(s.get("commentCount", 0)),
        })
    return out


# --------------------------------------------------------------------------- Instagram
GRAPH = "https://graph.facebook.com/v21.0"


def instagram_hashtag_top(hashtag: str, limit: int = 25) -> list[dict]:
    if not (settings.ig_user_id and settings.ig_access_token):
        log.info("Instagram credentials not set; skipping Instagram trends")
        return []
    auth = {"access_token": settings.ig_access_token}
    r = requests.get(f"{GRAPH}/ig_hashtag_search",
                     params={**auth, "user_id": settings.ig_user_id, "q": hashtag}, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json().get("data", [])
    if not data:
        return []
    r = requests.get(
        f"{GRAPH}/{data[0]['id']}/top_media",
        params={**auth, "user_id": settings.ig_user_id, "limit": limit,
                "fields": "id,caption,media_type,like_count,comments_count,timestamp"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    out = []
    for m in r.json().get("data", []):
        caption = m.get("caption") or ""
        out.append({
            "id": m["id"], "title": caption[:150], "published_at": m.get("timestamp", ""),
            "tags": [w[1:] for w in caption.split() if w.startswith("#")],
            # IG does not expose view counts for other accounts; likes are the proxy.
            "views": int(m.get("like_count", 0)) * 20,
            "likes": int(m.get("like_count", 0)), "comments": int(m.get("comments_count", 0)),
        })
    return out


def instagram_media_metrics(media_id: str) -> dict:
    r = requests.get(
        f"{GRAPH}/{media_id}/insights",
        params={"access_token": settings.ig_access_token, "metric": "views,likes,comments,shares"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    vals = {d["name"]: d["values"][0]["value"] for d in r.json().get("data", [])}
    return {k: vals.get(k) for k in ("views", "likes", "comments", "shares")}


# --------------------------------------------------------------------------- TikTok
TIKTOK = "https://open.tiktokapis.com/v2"


def tiktok_own_videos(max_count: int = 20) -> list[dict]:
    from . import tiktok_auth

    token = tiktok_auth.access_token()
    if not token:
        log.info("Not signed in to TikTok; skipping TikTok analytics")
        return []
    r = requests.post(
        f"{TIKTOK}/video/list/",
        params={"fields": "id,title,create_time,view_count,like_count,comment_count,share_count"},
        headers={"Authorization": f"Bearer {token}"},
        json={"max_count": max_count},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    out = []
    for v in r.json().get("data", {}).get("videos", []):
        out.append({
            "id": v["id"], "title": v.get("title", ""), "views": v.get("view_count", 0),
            "likes": v.get("like_count", 0), "comments": v.get("comment_count", 0),
            "shares": v.get("share_count", 0),
        })
    return out


def collect(db) -> int:
    """Pull trend metadata from every configured platform into the DB."""
    n = 0
    for term in settings.search_terms:
        term = term.strip()
        if not term:
            continue
        try:
            for item in youtube_trending_shorts(term):
                db.upsert_trend("youtube", item)
                n += 1
        except requests.HTTPError as e:
            log.warning("YouTube search failed for %r: %s", term, e)
    for tag in ("gta6", "gtavi", "gta6gameplay"):
        try:
            for item in instagram_hashtag_top(tag):
                db.upsert_trend("instagram", item)
                n += 1
        except requests.HTTPError as e:
            log.warning("Instagram hashtag search failed for %r: %s", tag, e)
    return n
