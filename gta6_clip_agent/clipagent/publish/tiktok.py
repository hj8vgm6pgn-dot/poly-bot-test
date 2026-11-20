"""TikTok upload via the Content Posting API.

Uses the *inbox* (draft) flow: the video lands in your TikTok app's inbox and you
finish posting from your phone (add a trending sound, adjust the caption). Direct
posting (`/post/publish/video/init/`) only works for apps that passed TikTok's
audit; until then direct posts are forced to private. Scope needed: video.upload.
"""
from __future__ import annotations

import math
from pathlib import Path

import requests

from ..config import settings

TIKTOK = "https://open.tiktokapis.com/v2"
TIMEOUT = 60
MAX_SINGLE = 64 * 1024 * 1024
CHUNK = 10 * 1024 * 1024


def upload_draft(clip: dict) -> str:
    if not settings.tiktok_access_token:
        raise RuntimeError("TIKTOK_ACCESS_TOKEN is not configured")
    path = Path(clip["rendered_path"])
    size = path.stat().st_size
    if size <= MAX_SINGLE:
        chunk_size, n_chunks = size, 1
    else:
        chunk_size = CHUNK
        n_chunks = math.floor(size / chunk_size)  # last chunk absorbs the remainder

    headers = {"Authorization": f"Bearer {settings.tiktok_access_token}",
               "Content-Type": "application/json; charset=UTF-8"}
    init = requests.post(
        f"{TIKTOK}/post/publish/inbox/video/init/",
        headers=headers,
        json={"source_info": {"source": "FILE_UPLOAD", "video_size": size,
                              "chunk_size": chunk_size, "total_chunk_count": n_chunks}},
        timeout=TIMEOUT,
    )
    init.raise_for_status()
    data = init.json()["data"]
    upload_url, publish_id = data["upload_url"], data["publish_id"]

    with path.open("rb") as f:
        for i in range(n_chunks):
            start = i * chunk_size
            end = size - 1 if i == n_chunks - 1 else start + chunk_size - 1
            f.seek(start)
            body = f.read(end - start + 1)
            r = requests.put(
                upload_url,
                headers={"Content-Type": "video/mp4", "Content-Length": str(len(body)),
                         "Content-Range": f"bytes {start}-{end}/{size}"},
                data=body,
                timeout=600,
            )
            r.raise_for_status()
    return publish_id
