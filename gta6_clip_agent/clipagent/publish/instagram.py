"""Instagram Reels publishing via the Graph API (Business/Creator account required).

The Graph API pulls the video from a public URL, so rendered clips must first be
uploaded somewhere reachable (S3, Cloudflare R2, etc.). Set PUBLIC_VIDEO_BASE_URL
to the folder URL that mirrors your OUTPUT_DIR/rendered directory.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from ..config import settings

GRAPH = "https://graph.facebook.com/v21.0"
TIMEOUT = 60


def publish_reel(clip: dict, poll_seconds: int = 300) -> str:
    if not (settings.ig_user_id and settings.ig_access_token):
        raise RuntimeError("Instagram credentials are not configured")
    if not settings.public_video_base_url:
        raise RuntimeError("PUBLIC_VIDEO_BASE_URL is required for Instagram (video must be at a public URL)")

    video_url = f"{settings.public_video_base_url.rstrip('/')}/{Path(clip['rendered_path']).name}"
    tags = json.loads(clip["hashtags"] or "[]")
    caption = f"{clip['caption']}\n\n" + " ".join(f"#{t}" for t in tags)
    auth = {"access_token": settings.ig_access_token}

    r = requests.post(
        f"{GRAPH}/{settings.ig_user_id}/media",
        data={**auth, "media_type": "REELS", "video_url": video_url, "caption": caption[:2200],
              "share_to_feed": "true"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    container_id = r.json()["id"]

    deadline = time.time() + poll_seconds
    while time.time() < deadline:
        s = requests.get(f"{GRAPH}/{container_id}", params={**auth, "fields": "status_code"}, timeout=TIMEOUT)
        s.raise_for_status()
        status = s.json().get("status_code")
        if status == "FINISHED":
            break
        if status == "ERROR":
            raise RuntimeError(f"Instagram failed to process the video: {s.json()}")
        time.sleep(10)
    else:
        raise TimeoutError("Instagram is still processing the video; try publishing again later")

    p = requests.post(f"{GRAPH}/{settings.ig_user_id}/media_publish",
                      data={**auth, "creation_id": container_id}, timeout=TIMEOUT)
    p.raise_for_status()
    return p.json()["id"]
