"""YouTube Shorts upload via the Data API v3 resumable upload.

A video uploaded with a vertical aspect ratio and duration <= 3 minutes is
treated as a Short automatically; "#Shorts" in the title/description helps.
Needs an OAuth refresh token with the youtube.upload scope.
"""
from __future__ import annotations

import json
from pathlib import Path

import requests

from ..config import settings
from .common import full_caption, tags as clip_tags

TIMEOUT = 60


def _access_token() -> str:
    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": settings.youtube_client_id,
            "client_secret": settings.youtube_client_secret,
            "refresh_token": settings.youtube_refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def upload_short(clip: dict, privacy: str = "private") -> str:
    if not (settings.youtube_client_id and settings.youtube_refresh_token):
        raise RuntimeError("YouTube OAuth credentials are not configured")
    path = Path(clip["rendered_path"])
    tags = clip_tags(clip)
    title = clip["title"][:90]
    if "#shorts" not in title.lower():
        title = f"{title} #Shorts"[:100]
    body = {
        "snippet": {
            "title": title,
            "description": full_caption(clip),
            "tags": tags,
            "categoryId": "20",  # Gaming
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    token = _access_token()
    init = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos",
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(path.stat().st_size),
        },
        data=json.dumps(body),
        timeout=TIMEOUT,
    )
    init.raise_for_status()
    with path.open("rb") as f:
        up = requests.put(
            init.headers["Location"],
            headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4"},
            data=f,
            timeout=600,
        )
    up.raise_for_status()
    return up.json()["id"]


def video_metrics(video_id: str) -> dict:
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"key": settings.youtube_api_key, "part": "statistics", "id": video_id},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    items = r.json().get("items", [])
    if not items:
        return {}
    s = items[0]["statistics"]
    return {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
            "comments": int(s.get("commentCount", 0)), "shares": None}
