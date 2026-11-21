"""Shared caption building so every platform carries the creator credit."""
from __future__ import annotations

import json


def tags(clip: dict) -> list[str]:
    return [t.lstrip("#") for t in json.loads(clip.get("hashtags") or "[]")]


def full_caption(clip: dict) -> str:
    parts = [clip.get("caption") or ""]
    if clip.get("credit"):
        parts.append(clip["credit"])
    hashtags = " ".join(f"#{t}" for t in tags(clip))
    if hashtags:
        parts.append(hashtags)
    return "\n\n".join(p for p in parts if p)
